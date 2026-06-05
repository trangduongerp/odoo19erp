from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from datetime import timedelta, datetime
import logging

_logger = logging.getLogger(__name__)


class HrAttendanceViolation(models.Model):
    _name = 'hr.attendance.violation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Attendance Violation'
    _order = 'date desc, employee_id'

    # ── Fields ──────────────────────────────────────────────────────────
    attendance_id = fields.Many2one('hr.attendance', string='Ban ghi cham cong')
    employee_id = fields.Many2one(
        'hr.employee', string='Nhan vien',
        required=True, tracking=True
    )
    rule_id = fields.Many2one('hr.attendance.rule', string='Rule vi pham')
    date = fields.Date('Ngay vi pham', required=True, default=fields.Date.today)

    violation_type = fields.Selection([
        ('late', 'Di muon'),
        ('early_leave', 'Ve som'),
        ('absence', 'Vang mat'),
        ('insufficient_hours', 'Thieu gio'),
    ], string='Loai vi pham', required=True, default='late', tracking=True)

    severity = fields.Selection([
        ('minor', 'Nhe'),
        ('moderate', 'Vua'),
        ('major', 'Nang'),
    ], string='Muc do', default='minor', tracking=True)

    deviation_minutes = fields.Float('Phut vi pham', default=0.0)
    penalty_points = fields.Integer('Diem vi pham', default=0)
    penalty_amount = fields.Float('Tien phat (VND)', tracking=True, default=0.0)

    state = fields.Selection([
        ('detected', 'Phat hien'),
        ('pending_explanation', 'Cho giai trinh'),
        ('explained', 'Da giai trinh'),
        ('confirmed', 'Xac nhan vi pham'),
        ('waived', 'Bo qua'),
        ('penalized', 'Da tru luong'),
    ], default='detected', tracking=True, string='Trang thai')

    explanation_id = fields.One2many(
        'hr.attendance.explanation', 'violation_id', string='Giai trinh'
    )

    month = fields.Integer(compute='_compute_period', store=True)
    year = fields.Integer(compute='_compute_period', store=True)
    monthly_same_type_count = fields.Integer(
        compute='_compute_monthly_count', store=True
    )
    is_escalated = fields.Boolean('Da leo thang', default=False, tracking=True)
    department_id = fields.Many2one(
        'hr.department',
        related='employee_id.department_id',
        store=True,
        string='Phong ban'
    )

    # ── Computed ────────────────────────────────────────────────────────
    @api.depends('date')
    def _compute_period(self):
        for rec in self:
            if rec.date:
                rec.month = rec.date.month
                rec.year = rec.date.year
            else:
                rec.month = 0
                rec.year = 0

    @api.depends('employee_id', 'violation_type', 'month', 'year')
    def _compute_monthly_count(self):
        for rec in self:
            if not rec.employee_id or not rec.month:
                rec.monthly_same_type_count = 0
                continue
            rec.monthly_same_type_count = self.search_count([
                ('employee_id', '=', rec.employee_id.id),
                ('violation_type', '=', rec.violation_type),
                ('month', '=', rec.month),
                ('year', '=', rec.year),
                ('state', 'not in', ['waived']),
            ])

    # ── Actions ─────────────────────────────────────────────────────────
    def action_request_explanation(self):
        for rec in self:
            if rec.state != 'detected':
                continue
            rec.write({'state': 'pending_explanation'})
            deadline_hours = (rec.rule_id.explanation_deadline_hours or 48) if rec.rule_id else 48
            deadline_date = fields.Date.today() + timedelta(hours=deadline_hours)
            if rec.employee_id.user_id:
                rec.activity_schedule(
                    'mail.mail_activity_data_todo',
                    note=(
                        f'Vi pham cham cong ngay {rec.date}: '
                        f'{rec.get_violation_type_label()} '
                        f'({rec.deviation_minutes:.0f} phut). '
                        f'Vui long giai trinh truoc {deadline_date}.'
                    ),
                    user_id=rec.employee_id.user_id.id,
                    date_deadline=deadline_date,
                )
            rec.message_post(
                body=(
                    f'<p>Vi pham da duoc ghi nhan: <b>{rec.get_violation_type_label()}</b></p>'
                    f'<p>Tien phat du kien: <b>{rec.penalty_amount:,.0f} VND</b></p>'
                    f'<p>Vui long giai trinh neu co ly do hop le.</p>'
                )
            )

    def action_confirm(self):
        for rec in self:
            rec.write({'state': 'confirmed'})
            rec._check_escalation()

    def action_waive(self):
        for rec in self:
            rec.write({'state': 'waived', 'penalty_amount': 0})
            try:
                rec.activity_feedback(
                    ['hr_attendance_control.activity_violation_explanation'],
                    feedback='Vi pham da duoc bo qua boi Manager.'
                )
            except Exception:
                pass
            rec.message_post(
                body='<p>Vi pham da duoc <b>bo qua</b>. Khong tru luong.</p>'
            )

    def _check_escalation(self):
        if self.severity != 'minor':
            return
        if self.monthly_same_type_count < 3:
            return
        if self.is_escalated:
            return
        self.write({'severity': 'major', 'is_escalated': True})
        try:
            hr_group = self.env.ref('hr.group_hr_manager')
            for user in hr_group.users:
                self.activity_schedule(
                    'mail.mail_activity_data_warning',
                    note=(
                        f'Nhan vien {self.employee_id.name} da vi pham '
                        f'"{self.get_violation_type_label()}" '
                        f'{self.monthly_same_type_count} lan trong thang '
                        f'{self.month}/{self.year}. Tu dong leo thang len muc Nang.'
                    ),
                    user_id=user.id,
                )
        except Exception as e:
            _logger.warning('Escalation notification failed: %s', e)

    def get_violation_type_label(self):
        labels = {
            'late': 'Di muon',
            'early_leave': 'Ve som',
            'absence': 'Vang mat',
            'insufficient_hours': 'Thieu gio lam',
        }
        return labels.get(self.violation_type, self.violation_type)

    # ── Cronjob: phat hien vang mat ─────────────────────────────────────
    @api.model
    def _cron_detect_absence(self):
        yesterday = fields.Date.today() - timedelta(days=1)
        weekday = yesterday.weekday()
        if weekday >= 5:
            return

        absence_rule = self.env['hr.attendance.rule'].search([
            ('violation_type', '=', 'absence'),
            ('is_active', '=', True),
        ], limit=1)
        if not absence_rule:
            _logger.warning('No active absence rule found.')
            return

        employees = self.env['hr.employee'].search([('active', '=', True)])
        created_count = 0

        for employee in employees:
            schedule = employee.resource_calendar_id
            if not schedule:
                continue

            work_lines = schedule.attendance_ids.filtered(
                lambda a: int(a.dayofweek) == weekday and a.day_period != 'lunch'
            )
            if not work_lines:
                continue

            has_attendance = self.env['hr.attendance'].search_count([
                ('employee_id', '=', employee.id),
                ('check_in', '>=', datetime.combine(yesterday, datetime.min.time())),
                ('check_in', '<', datetime.combine(
                    yesterday + timedelta(days=1), datetime.min.time()
                )),
            ])
            if has_attendance:
                continue

            approved_leave = self.env['hr.leave'].search_count([
                ('employee_id', '=', employee.id),
                ('state', '=', 'validate'),
                ('date_from', '<=', fields.Datetime.from_string(f'{yesterday} 23:59:59')),
                ('date_to', '>=', fields.Datetime.from_string(f'{yesterday} 00:00:00')),
            ])
            if approved_leave:
                continue

            existing = self.search_count([
                ('employee_id', '=', employee.id),
                ('date', '=', yesterday),
                ('violation_type', '=', 'absence'),
            ])
            if existing:
                continue

            # Tinh tien phat vang mat
            # OCA Payroll 19: wage luu truc tiep tren hr.employee
            wage = 0.0
            try:
                if hasattr(employee, 'wage') and employee.wage:
                    wage = float(employee.wage)
            except Exception:
                pass
            if not wage:
                try:
                    if hasattr(employee, 'contract_wage') and employee.contract_wage:
                        wage = float(employee.contract_wage)
                except Exception:
                    pass
            if not wage and 'hr.contract' in self.env:
                try:
                    c = self.env['hr.contract'].search([
                        ('employee_id', '=', employee.id),
                    ], order='date_start desc', limit=1)
                    if c and hasattr(c, 'wage'):
                        wage = float(c.wage or 0)
                except Exception:
                    pass
            penalty = 0.0
            if wage:
                daily_wage = wage / 26
                if absence_rule.penalty_type == 'percent_daily':
                    penalty = daily_wage * (absence_rule.penalty_value or 100) / 100
                elif absence_rule.penalty_type == 'fixed':
                    penalty = absence_rule.penalty_value

            try:
                violation = self.create({
                    'employee_id': employee.id,
                    'rule_id': absence_rule.id,
                    'date': yesterday,
                    'violation_type': 'absence',
                    'severity': absence_rule.severity,
                    'deviation_minutes': 480,
                    'penalty_points': absence_rule.penalty_points,
                    'penalty_amount': penalty,
                    'state': 'detected',
                })
                violation.action_request_explanation()
                created_count += 1
            except Exception as e:
                _logger.error(
                    'Failed to create absence violation for %s: %s',
                    employee.name, str(e)
                )

        _logger.info(
            'Absence detection done: %d violations for %s',
            created_count, yesterday
        )

    # ── Mo form giai trinh ───────────────────────────────────────────────
    def action_open_explanation_form(self):
        """
        Mo form Giai trinh de nhan vien nhap ly do.
        Dung self.id (integer that trong DB) thay vi context tu One2many
        (Odoo 17: One2many truyen NewId ao, khong phai integer that).
        """
        self.ensure_one()
        existing = self.env['hr.attendance.explanation'].search([
            ('violation_id', '=', self.id)
        ], limit=1)

        if existing:
            # Da co giai trinh: mo de xem/sua
            return {
                'type': 'ir.actions.act_window',
                'name': 'Giai trinh vi pham',
                'res_model': 'hr.attendance.explanation',
                'res_id': existing.id,
                'view_mode': 'form',
                'target': 'new',
            }

        # Chua co: tao moi voi violation_id la integer that
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tao giai trinh',
            'res_model': 'hr.attendance.explanation',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_violation_id': self.id,       # integer that, khong phai NewId
                'default_employee_id': self.employee_id.id,
            },
        }

    def action_open_explanation_review(self):
        """
        Mo wizard duyet giai trinh danh cho Manager.

        Dung TransientModel wizard thay vi mo form hr.attendance.explanation truc tiep.
        Ly do: Odoo 19 luon mo dialog o READONLY voi existing record (res_id),
        khong co cach ep edit mode tu client. TransientModel tao record moi →
        form luon mo o EDIT mode → Manager nhap duoc manager_note.
        """
        self.ensure_one()
        explanation = self.env['hr.attendance.explanation'].search([
            ('violation_id', '=', self.id)
        ], limit=1)

        if not explanation:
            raise UserError('Chua co giai trinh nao cho vi pham nay.')
        if explanation.state != 'submitted':
            raise UserError(
                f'Giai trinh dang o trang thai "{explanation.state}". '
                'Chi co the duyet giai trinh o trang thai "Da gui - Cho review".'
            )

        return {
            'type': 'ir.actions.act_window',
            'name': 'Duyet giai trinh vi pham',
            'res_model': 'hr.explanation.review.wizard',
            'view_mode': 'form',
            'target': 'new',
            # Khong co res_id → Odoo tao TransientModel record moi → luon EDIT mode
            'context': {
                'default_explanation_id': explanation.id,
            },
        }
