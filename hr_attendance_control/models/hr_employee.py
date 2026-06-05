from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # ── Computed fields cho smart button ──────────────────────────────
    violation_count = fields.Integer(
        compute='_compute_violation_stats',
        string='Vi pham'
    )
    pending_violation_count = fields.Integer(
        compute='_compute_violation_stats',
        string='Cho xu ly'
    )
    current_month_points = fields.Integer(
        compute='_compute_violation_stats',
        string='Diem vi pham thang nay'
    )
    violation_ids = fields.One2many(
        'hr.attendance.violation',
        'employee_id',
        string='Vi pham cham cong'
    )

    def _compute_violation_stats(self):
        today = fields.Date.today()
        for emp in self:
            all_violations = self.env['hr.attendance.violation'].search([
                ('employee_id', '=', emp.id),
                ('state', 'not in', ['waived']),
            ])
            pending = all_violations.filtered(
                lambda v: v.state in [
                    'detected', 'pending_explanation', 'explained'
                ]
            )
            this_month = all_violations.filtered(
                lambda v: v.month == today.month
                and v.year == today.year
                and v.state == 'confirmed'
            )
            emp.violation_count = len(all_violations)
            emp.pending_violation_count = len(pending)
            emp.current_month_points = sum(
                this_month.mapped('penalty_points')
            )

    def action_view_violations(self):
        """Smart button mo danh sach vi pham cua nhan vien"""
        return {
            'name': f'Vi pham — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'hr.attendance.violation',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }
