from odoo import api, fields, models
from odoo.exceptions import ValidationError
from datetime import timedelta, datetime
import logging

_logger = logging.getLogger(__name__)


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    violation_ids = fields.One2many(
        'hr.attendance.violation',
        'attendance_id',
        string='Vi pham phat sinh'
    )
    has_violation = fields.Boolean(
        compute='_compute_has_violation',
        store=True,
        string='Co vi pham'
    )

    @api.depends('violation_ids')
    def _compute_has_violation(self):
        for rec in self:
            rec.has_violation = bool(rec.violation_ids)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.check_out and not rec.violation_ids:
                try:
                    rec._detect_and_create_violations()
                except Exception as e:
                    _logger.error(
                        'Violation detection failed for %s: %s',
                        rec.employee_id.name, str(e)
                    )
        return records

    def write(self, vals):
        result = super().write(vals)
        if 'check_out' in vals:
            for rec in self:
                if rec.check_out and not rec.violation_ids:
                    try:
                        rec._detect_and_create_violations()
                    except Exception as e:
                        _logger.error(
                            'Violation detection failed for %s: %s',
                            rec.employee_id.name, str(e)
                        )
        return result

    def _detect_and_create_violations(self):
        self.ensure_one()
        employee = self.employee_id
        schedule = employee.resource_calendar_id
        if not schedule:
            _logger.warning(
                'No work schedule for %s, skipping.', employee.name
            )
            return

        attendance_date = self.check_in.date()
        planned = self._get_planned_attendance(schedule, attendance_date)
        if not planned:
            _logger.info(
                '%s: %s is not a working day.', employee.name, attendance_date
            )
            return

        planned_start, planned_end = planned
        check_in_local = self._to_local_time(self.check_in)
        check_out_local = self._to_local_time(self.check_out)

        _logger.info(
            'Detection for %s | planned %s-%s | actual %s-%s',
            employee.name,
            planned_start.strftime('%H:%M'),
            planned_end.strftime('%H:%M'),
            check_in_local.strftime('%H:%M'),
            check_out_local.strftime('%H:%M'),
        )

        active_rules = self.env['hr.attendance.rule'].search([
            ('is_active', '=', True),
            ('violation_type', 'in', ['late', 'early_leave', 'insufficient_hours']),
        ])
        applicable_rules = active_rules.filtered(
            lambda r: r.applies_to_employee(employee)
        )

        violations_created = []
        for rule in applicable_rules:
            violation_data = None

            if rule.violation_type == 'late':
                late_seconds = (check_in_local - planned_start).total_seconds()
                late_minutes = max(0, late_seconds / 60 - (rule.grace_period_minutes or 0))
                if late_minutes >= rule.threshold_minutes:
                    max_ok = rule.threshold_minutes_max
                    if not max_ok or late_minutes < max_ok:
                        violation_data = {
                            'violation_type': 'late',
                            'deviation_minutes': late_minutes,
                        }

            elif rule.violation_type == 'early_leave':
                early_seconds = (planned_end - check_out_local).total_seconds()
                early_minutes = max(0, early_seconds / 60 - (rule.grace_period_minutes or 0))
                if early_minutes >= rule.threshold_minutes:
                    max_ok = rule.threshold_minutes_max
                    if not max_ok or early_minutes < max_ok:
                        violation_data = {
                            'violation_type': 'early_leave',
                            'deviation_minutes': early_minutes,
                        }

            elif rule.violation_type == 'insufficient_hours':
                planned_hours = (planned_end - planned_start).total_seconds() / 3600
                shortage = planned_hours - self.worked_hours
                if shortage * 60 >= rule.threshold_minutes:
                    violation_data = {
                        'violation_type': 'insufficient_hours',
                        'deviation_minutes': shortage * 60,
                    }

            if violation_data:
                penalty = self._calculate_penalty(rule, employee, violation_data)
                violation = self.env['hr.attendance.violation'].create({
                    'attendance_id': self.id,
                    'employee_id': employee.id,
                    'rule_id': rule.id,
                    'date': attendance_date,
                    'violation_type': violation_data['violation_type'],
                    'severity': rule.severity,
                    'deviation_minutes': violation_data['deviation_minutes'],
                    'penalty_points': rule.penalty_points,
                    'penalty_amount': penalty,
                    'state': 'detected',
                })
                violations_created.append(violation)
                _logger.info(
                    'Violation created: %s %s %.1f min %.0f VND',
                    employee.name,
                    violation_data['violation_type'],
                    violation_data['deviation_minutes'],
                    penalty,
                )

        if violations_created:
            self._notify_violations(
                self.env['hr.attendance.violation'].browse(
                    [v.id for v in violations_created]
                )
            )

    def _get_planned_attendance(self, schedule, work_date):
        """
        Lay gio lam viec theo lich, BO QUA cac dong Break (day_period = lunch).
        Tra ve (start_local_naive, end_local_naive) hoac None.
        """
        import pytz
        weekday = work_date.weekday()

        work_lines = schedule.attendance_ids.filtered(
            lambda a: int(a.dayofweek) == weekday
            and a.day_period != 'lunch'
        )
        if not work_lines:
            return None

        tz_name = schedule.tz or 'Asia/Ho_Chi_Minh'
        tz = pytz.timezone(tz_name)

        def float_to_local_naive(hour_float, d):
            h = int(hour_float)
            m = int(round((hour_float - h) * 60))
            naive = datetime(d.year, d.month, d.day, h, m, 0)
            # Chuyen sang UTC roi boc lai thanh naive de so sanh
            local_aware = tz.localize(naive)
            return local_aware.replace(tzinfo=None)

        min_hour = min(a.hour_from for a in work_lines)
        max_hour = max(a.hour_to for a in work_lines)
        return float_to_local_naive(min_hour, work_date), float_to_local_naive(max_hour, work_date)

    def _to_local_time(self, utc_dt):
        """
        Convert UTC datetime sang local naive datetime
        theo timezone cua work schedule.
        """
        import pytz
        schedule = self.employee_id.resource_calendar_id
        tz_name = (schedule and schedule.tz) or 'Asia/Ho_Chi_Minh'
        tz = pytz.timezone(tz_name)
        return utc_dt.replace(tzinfo=pytz.utc).astimezone(tz).replace(tzinfo=None)

    def _calculate_penalty(self, rule, employee, violation_data):
        """
        Tinh tien phat dua tren wage cua nhan vien.
        Tim wage theo thu tu uu tien:
        1. hr.contract (neu co model nay trong he thong)
        2. employee.contract_id.wage
        3. Payslip gan nhat
        4. Rule penalty_value co dinh
        """
        wage = self._get_employee_wage(employee)

        if not wage:
            if rule.penalty_type == 'fixed':
                return rule.penalty_value
            _logger.warning(
                'No wage found for %s, penalty = 0', employee.name
            )
            return 0.0

        daily_wage = wage / 26
        hourly_wage = daily_wage / 8

        if rule.penalty_type == 'fixed':
            return rule.penalty_value
        elif rule.penalty_type == 'percent_daily':
            return daily_wage * rule.penalty_value / 100
        elif rule.penalty_type == 'percent_hourly':
            shortage_hours = violation_data.get('deviation_minutes', 0) / 60
            return hourly_wage * shortage_hours * (rule.penalty_value or 100) / 100
        return 0.0

    def _get_employee_wage(self, employee):
        """
        Lay wage cua nhan vien.
        OCA Payroll 19 luu wage truc tiep tren hr.employee:
          - employee.wage          : luong chinh
          - employee.contract_wage : luong hop dong (neu khac)
        """
        wage = 0.0

        # Cach 1 (uu tien): employee.wage — field truc tiep tren hr.employee
        # OCA Payroll 19 add field nay vao hr.employee
        try:
            if hasattr(employee, 'wage') and employee.wage:
                wage = float(employee.wage)
        except Exception as e:
            _logger.debug('employee.wage failed: %s', e)

        # Cach 2: employee.contract_wage
        if not wage:
            try:
                if hasattr(employee, 'contract_wage') and employee.contract_wage:
                    wage = float(employee.contract_wage)
            except Exception as e:
                _logger.debug('employee.contract_wage failed: %s', e)

        # Cach 3: hr.contract neu co model
        if not wage and 'hr.contract' in self.env:
            try:
                contract = self.env['hr.contract'].search([
                    ('employee_id', '=', employee.id),
                ], order='date_start desc', limit=1)
                if contract and hasattr(contract, 'wage'):
                    wage = float(contract.wage or 0)
            except Exception as e:
                _logger.debug('hr.contract failed: %s', e)

        if not wage:
            _logger.warning(
                'Cannot determine wage for %s, penalty will be 0.', employee.name
            )
        return wage

    def _notify_violations(self, violations):
        employee = self.employee_id
        manager = employee.parent_id
        violation_lines = ''.join([
            f'<li>{v.get_violation_type_label()}: '
            f'{v.deviation_minutes:.0f} phut - '
            f'Tien phat: {v.penalty_amount:,.0f} VND</li>'
            for v in violations
        ])
        body = (
            f'<p>Phat hien vi pham cham cong ngay '
            f'<b>{violations[0].date}</b>:</p>'
            f'<ul>{violation_lines}</ul>'
            f'<p>Vui long giai trinh neu co ly do hop le.</p>'
        )
        partner_ids = []
        if employee.user_id:
            partner_ids.append(employee.user_id.partner_id.id)
        if manager and manager.user_id:
            partner_ids.append(manager.user_id.partner_id.id)
        if partner_ids:
            violations[0].message_post(body=body, partner_ids=partner_ids)
        if violations[0].rule_id and violations[0].rule_id.requires_explanation \
                and employee.user_id:
            violations[0].activity_schedule(
                'mail.mail_activity_data_todo',
                note=(
                    f'Vui long giai trinh vi pham cham cong '
                    f'ngay {violations[0].date}'
                ),
                user_id=employee.user_id.id,
                date_deadline=fields.Date.today() + timedelta(days=2),
            )
