from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from datetime import date
import logging

_logger = logging.getLogger(__name__)


class HrAttendancePenaltySummary(models.Model):
    _name = 'hr.attendance.penalty.summary'
    _description = 'Attendance Penalty Monthly Summary'
    _inherit = ['mail.thread']
    _order = 'year desc, month desc, employee_id'

    employee_id = fields.Many2one(
        'hr.employee', string='Nhan vien',
        required=True, ondelete='cascade', tracking=True,
    )
    month = fields.Integer('Thang', required=True)
    year = fields.Integer('Nam', required=True)

    # ── Computed stats: KHONG store=True de luon tinh lai moi nhat ──────────
    # Ly do: neu store=True, gia tri bi cache tu luc tao record. Khi vi pham
    # duoc xac nhan (state -> confirmed) sau do, ban tong hop se khong tu dong
    # cap nhat → hien thi 0 du co vi pham.
    total_violations = fields.Integer(
        'Tong so vi pham',
        compute='_compute_stats',
        store=False,
    )
    minor_count = fields.Integer(
        'Vi pham nhe',
        compute='_compute_stats',
        store=False,
    )
    moderate_count = fields.Integer(
        'Vi pham vua',
        compute='_compute_stats',
        store=False,
    )
    major_count = fields.Integer(
        'Vi pham nang',
        compute='_compute_stats',
        store=False,
    )
    total_points = fields.Integer(
        'Tong diem vi pham',
        compute='_compute_stats',
        store=False,
    )
    total_deduction = fields.Float(
        'Tong khau tru (VND)',
        compute='_compute_stats',
        store=False,
        tracking=True,
    )
    late_minutes_total = fields.Float(
        'Tong phut di muon',
        compute='_compute_stats',
        store=False,
    )
    absence_days = fields.Integer(
        'Ngay vang mat khong phep',
        compute='_compute_stats',
        store=False,
    )

    state = fields.Selection([
        ('draft', 'Nhap'),
        ('confirmed', 'Da xac nhan'),
        ('pushed', 'Da day vao luong'),
    ], default='draft', tracking=True)

    pushed_to_payroll = fields.Boolean('Da day vao Payroll', default=False, tracking=True)
    payslip_id = fields.Many2one('hr.payslip', 'Phieu luong lien quan', readonly=True)

    penalty_display_name = fields.Char(
        string='Ten hien thi',
        compute='_compute_penalty_display_name',
        store=True,
    )
    violation_ids = fields.One2many(
        'hr.attendance.violation',
        compute='_compute_violation_ids',
        string='Chi tiet vi pham',
    )

    @api.depends('employee_id', 'month', 'year')
    def _compute_penalty_display_name(self):
        for rec in self:
            emp = rec.employee_id.name or ''
            rec.penalty_display_name = f'{emp} - {rec.month:02d}/{rec.year}'

    def _compute_violation_ids(self):
        for rec in self:
            rec.violation_ids = self.env['hr.attendance.violation'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('month', '=', rec.month),
                ('year', '=', rec.year),
                ('state', '=', 'confirmed'),
            ])

    def _compute_stats(self):
        """
        Luon tinh lai truc tiep tu DB, khong dung cache.
        store=False dam bao gia tri luon la moi nhat.
        """
        for rec in self:
            if not rec.employee_id or not rec.month or not rec.year:
                rec.total_violations = 0
                rec.minor_count = 0
                rec.moderate_count = 0
                rec.major_count = 0
                rec.total_points = 0
                rec.total_deduction = 0.0
                rec.late_minutes_total = 0.0
                rec.absence_days = 0
                continue

            violations = self.env['hr.attendance.violation'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('month', '=', rec.month),
                ('year', '=', rec.year),
                ('state', '=', 'confirmed'),
            ])
            rec.total_violations = len(violations)
            rec.minor_count = len(violations.filtered(lambda v: v.severity == 'minor'))
            rec.moderate_count = len(violations.filtered(lambda v: v.severity == 'moderate'))
            rec.major_count = len(violations.filtered(lambda v: v.severity == 'major'))
            rec.total_points = sum(violations.mapped('penalty_points'))
            rec.total_deduction = sum(violations.mapped('penalty_amount'))
            rec.late_minutes_total = sum(
                v.deviation_minutes for v in violations
                if v.violation_type == 'late'
            )
            rec.absence_days = len(violations.filtered(
                lambda v: v.violation_type == 'absence'
            ))

    @api.constrains('employee_id', 'month', 'year')
    def _check_unique_employee_month_year(self):
        for rec in self:
            duplicate = self.search([
                ('employee_id', '=', rec.employee_id.id),
                ('month', '=', rec.month),
                ('year', '=', rec.year),
                ('id', '!=', rec.id),
            ])
            if duplicate:
                raise ValidationError(
                    f'Nhan vien {rec.employee_id.name} da co ban tong hop '
                    f'cho thang {rec.month:02d}/{rec.year}!'
                )

    def action_confirm(self):
        """
        Xac nhan ban tong hop.
        KHONG dung total_violations (co the bi cache cu),
        thay vao do truy van truc tiep.
        """
        for rec in self:
            # Kiem tra truc tiep tu DB, bo qua gia tri cached
            violations = self.env['hr.attendance.violation'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('month', '=', rec.month),
                ('year', '=', rec.year),
                ('state', '=', 'confirmed'),
            ])
            if not violations:
                raise UserError(
                    f'Khong tim thay vi pham nao da duoc "Xac nhan vi pham" '
                    f'cho {rec.employee_id.name} trong thang {rec.month:02d}/{rec.year}.\n\n'
                    f'Vui long vao tung vi pham va bam "Xac nhan vi pham" truoc.'
                )
            rec.write({'state': 'confirmed'})
            rec.message_post(
                body=(
                    f'<p>Ban tong hop da duoc xac nhan voi '
                    f'<b>{len(violations)}</b> vi pham, '
                    f'tong khau tru: <b>{sum(violations.mapped("penalty_amount")):,.0f} VND</b>.</p>'
                )
            )

    def action_push_to_payroll(self):
        self.ensure_one()
        if self.pushed_to_payroll:
            raise UserError('Ban tong hop nay da duoc day vao Payroll roi.')
        if self.state != 'confirmed':
            raise UserError('Vui long xac nhan ban tong hop truoc khi day vao Payroll.')

        # Tinh lai truc tiep de lay so tien chinh xac
        violations = self.env['hr.attendance.violation'].search([
            ('employee_id', '=', self.employee_id.id),
            ('month', '=', self.month),
            ('year', '=', self.year),
            ('state', '=', 'confirmed'),
        ])
        total_deduction = sum(violations.mapped('penalty_amount'))

        if total_deduction <= 0:
            raise UserError('Khong co khoan khau tru nao de day.')

        payslip = self.env['hr.payslip'].search([
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ['draft', 'verify']),
            ('date_from', '<=', date(self.year, self.month, 28)),
            ('date_to', '>=', date(self.year, self.month, 1)),
        ], limit=1)

        if not payslip:
            raise UserError(
                f'Khong tim thay phieu luong cua {self.employee_id.name} '
                f'cho thang {self.month:02d}/{self.year}. '
                f'Vui long tao phieu luong truoc.'
            )

        self._inject_deduction_to_payslip(payslip, total_deduction)
        violations.write({'state': 'penalized'})
        self.write({
            'pushed_to_payroll': True,
            'state': 'pushed',
            'payslip_id': payslip.id,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Day vao Payroll thanh cong',
                'message': (
                    f'Da tao khoan khau tru {total_deduction:,.0f} VND '
                    f'vao phieu luong cua {self.employee_id.name}.'
                ),
                'type': 'success',
                'sticky': False,
            }
        }

    def _inject_deduction_to_payslip(self, payslip, total_deduction):
        deduction_rule = self.env['hr.salary.rule'].search([
            ('code', '=', 'ATTEND_DEDUCT'),
        ], limit=1)

        if not deduction_rule:
            ded_category = self.env['hr.salary.rule.category'].search([
                ('code', '=', 'DED'),
            ], limit=1)
            if not ded_category:
                ded_category = self.env['hr.salary.rule.category'].create({
                    'name': 'Deductions', 'code': 'DED',
                })
            struct = payslip.struct_id
            if not struct:
                raise UserError('Phieu luong chua co Salary Structure.')
            deduction_rule = self.env['hr.salary.rule'].create({
                'name': 'Khau tru vi pham cham cong',
                'code': 'ATTEND_DEDUCT',
                'category_id': ded_category.id,
                'sequence': 200,
                'amount_select': 'fix',
                'amount_fix': 0.0,
                'active': True,
            })
            # Gan rule vao struct (thay the struct_id da bi xoa khoi Odoo 16+)
            if deduction_rule not in struct.rule_ids:
                struct.write({'rule_ids': [(4, deduction_rule.id)]})

        existing = self.env['hr.payslip.line'].search([
            ('slip_id', '=', payslip.id),
            ('code', '=', 'ATTEND_DEDUCT'),
        ], limit=1)

        if existing:
            existing.write({'amount': -total_deduction, 'total': -total_deduction})
        else:
            self.env['hr.payslip.line'].create({
                'slip_id': payslip.id,
                'name': f'Khau tru vi pham cham cong (thang {self.month:02d}/{self.year})',
                'code': 'ATTEND_DEDUCT',
                'salary_rule_id': deduction_rule.id,
                'category_id': deduction_rule.category_id.id,
                'sequence': 200,
                'amount': -total_deduction,
                'total': -total_deduction,
                'quantity': 1.0,
                'rate': 100.0,
            })

    @api.model
    def _cron_generate_monthly_summary(self):
        today = date.today()
        if today.month == 1:
            target_month, target_year = 12, today.year - 1
        else:
            target_month, target_year = today.month - 1, today.year

        violations = self.env['hr.attendance.violation'].search([
            ('month', '=', target_month),
            ('year', '=', target_year),
            ('state', '=', 'confirmed'),
        ])

        employees = violations.mapped('employee_id')
        created_count = 0

        for employee in employees:
            existing = self.search([
                ('employee_id', '=', employee.id),
                ('month', '=', target_month),
                ('year', '=', target_year),
            ])
            if existing:
                continue
            try:
                summary = self.create({
                    'employee_id': employee.id,
                    'month': target_month,
                    'year': target_year,
                })
                summary.action_confirm()
                created_count += 1
            except Exception as e:
                _logger.error(
                    'Failed to create penalty summary for %s: %s',
                    employee.name, str(e)
                )

        _logger.info(
            'Monthly summary done: %d records for %02d/%d',
            created_count, target_month, target_year
        )
