from odoo import api, fields, models
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    attendance_deduction_amount = fields.Float(
        string='Khau tru vi pham cham cong',
        compute='_compute_attendance_deduction',
        store=True,
    )
    attendance_violation_count = fields.Integer(
        string='So vi pham',
        compute='_compute_attendance_deduction',
        store=True,
    )

    @api.depends('employee_id', 'date_from', 'date_to')
    def _compute_attendance_deduction(self):
        for rec in self:
            if not rec.employee_id or not rec.date_from or not rec.date_to:
                rec.attendance_deduction_amount = 0.0
                rec.attendance_violation_count = 0
                continue
            violations = self.env['hr.attendance.violation'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('state', '=', 'confirmed'),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
            ])
            rec.attendance_deduction_amount = sum(violations.mapped('penalty_amount'))
            rec.attendance_violation_count = len(violations)

    def action_payslip_done(self):
        result = super().action_payslip_done()
        for rec in self:
            violations = self.env['hr.attendance.violation'].search([
                ('employee_id', '=', rec.employee_id.id),
                ('state', '=', 'confirmed'),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
            ])
            if violations and rec.attendance_deduction_amount > 0:
                self._create_attendance_deduction_line(rec, rec.attendance_deduction_amount)
                violations.write({'state': 'penalized'})
        return result

    def _create_attendance_deduction_line(self, payslip, amount):
        deduction_rule = self.env['hr.salary.rule'].search([
            ('code', '=', 'ATTEND_DEDUCT'),
        ], limit=1)
        if not deduction_rule:
            ded_category = self.env['hr.salary.rule.category'].search([
                ('code', '=', 'DED'),
            ], limit=1)
            if not ded_category:
                ded_category = self.env['hr.salary.rule.category'].create({
                    'name': 'Deductions',
                    'code': 'DED',
                })
            struct = payslip.struct_id
            if not struct:
                _logger.warning('No struct on payslip, cannot create deduction rule.')
                return
            deduction_rule = self.env['hr.salary.rule'].create({
                'name': 'Khau tru vi pham cham cong',
                'code': 'ATTEND_DEDUCT',
                'category_id': ded_category.id,
                'struct_id': struct.id,
                'sequence': 200,
                'amount_select': 'fix',
                'amount_fix': 0.0,
                'active': True,
            })

        existing = self.env['hr.payslip.line'].search([
            ('payslip_id', '=', payslip.id),
            ('code', '=', 'ATTEND_DEDUCT'),
        ], limit=1)

        if existing:
            existing.write({'amount': -amount, 'total': -amount})
        else:
            self.env['hr.payslip.line'].create({
                'payslip_id': payslip.id,
                'name': f'Khau tru vi pham cham cong ({payslip.date_from} - {payslip.date_to})',
                'code': 'ATTEND_DEDUCT',
                'salary_rule_id': deduction_rule.id,
                'category_id': deduction_rule.category_id.id,
                'sequence': 200,
                'amount': -amount,
                'total': -amount,
                'quantity': 1.0,
                'rate': 100.0,
            })
