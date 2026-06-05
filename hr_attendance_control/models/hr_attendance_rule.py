from odoo import api, fields, models


class HrAttendanceRule(models.Model):
    _name = 'hr.attendance.rule'
    _description = 'Attendance Violation Rule'
    _order = 'violation_type, threshold_minutes'

    name = fields.Char('Ten rule', required=True)
    violation_type = fields.Selection([
        ('late', 'Di muon'),
        ('early_leave', 'Ve som'),
        ('absence', 'Vang mat'),
        ('insufficient_hours', 'Thieu gio lam'),
    ], string='Loai vi pham', required=True)

    threshold_minutes = fields.Float('Nguong toi thieu (phut)', default=0)
    threshold_minutes_max = fields.Float(
        'Nguong toi da (phut)',
        help='De trong neu khong co gioi han tren.'
    )
    grace_period_minutes = fields.Integer('Khoang tha thu (phut)', default=0)
    severity = fields.Selection([
        ('minor', 'Nhe'),
        ('moderate', 'Vua'),
        ('major', 'Nang'),
    ], string='Muc do', required=True, default='minor')

    penalty_points = fields.Integer('Diem vi pham', default=1)
    penalty_type = fields.Selection([
        ('fixed', 'Co dinh (VND)'),
        ('percent_daily', '% luong ngay'),
        ('percent_hourly', '% luong gio thieu'),
    ], string='Cach tinh phat', default='percent_daily')
    penalty_value = fields.Float('Gia tri phat', default=1.0)

    department_ids = fields.Many2many('hr.department', string='Phong ban ap dung')
    job_ids = fields.Many2many('hr.job', string='Chuc danh ap dung')

    requires_explanation = fields.Boolean('Yeu cau giai trinh', default=True)
    explanation_deadline_hours = fields.Integer('Deadline giai trinh (gio)', default=48)
    is_active = fields.Boolean('Dang kich hoat', default=True)

    def applies_to_employee(self, employee):
        if self.department_ids and employee.department_id not in self.department_ids:
            return False
        if self.job_ids and employee.job_id not in self.job_ids:
            return False
        return True
