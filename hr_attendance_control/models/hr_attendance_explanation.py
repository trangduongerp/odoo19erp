from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)


class HrAttendanceExplanation(models.Model):
    _name = 'hr.attendance.explanation'
    _description = 'Attendance Violation Explanation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'violation_id'
    _order = 'create_date desc'

    violation_id = fields.Many2one(
        'hr.attendance.violation',
        string='Vi pham lien quan',
        required=True,
        ondelete='cascade',
        tracking=True,
    )
    employee_id = fields.Many2one(
        'hr.employee',
        related='violation_id.employee_id',
        store=True,
        readonly=True,
        string='Nhan vien'
    )
    explanation_text = fields.Text(
        string='Noi dung giai trinh',
        required=True,
        tracking=True,
    )
    submitted_at = fields.Datetime('Thoi diem gui', readonly=True)
    deadline = fields.Datetime('Han giai trinh', readonly=True)
    is_overdue = fields.Boolean(compute='_compute_is_overdue', store=True)

    state = fields.Selection([
        ('draft', 'Dang soan'),
        ('submitted', 'Da gui - Cho review'),
        ('accepted', 'Duoc chap nhan'),
        ('rejected', 'Bi tu choi'),
    ], default='draft', string='Trang thai', tracking=True)

    manager_note = fields.Text('Ghi chu cua Manager', tracking=True)
    reviewed_by_id = fields.Many2one('res.users', string='Nguoi review', readonly=True)
    reviewed_at = fields.Datetime('Thoi diem review', readonly=True)

    @api.depends('deadline')
    def _compute_is_overdue(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_overdue = bool(
                rec.deadline and rec.deadline < now
                and rec.state in ('draft', 'submitted')
            )

    @api.model
    def default_get(self, fields_list):
        """
        Dam bao violation_id duoc lay tu context khi tao tu tab Giai trinh
        tren form Vi pham.
        """
        res = super().default_get(fields_list)
        # Odoo truyen default_violation_id qua context tu One2many field
        if 'violation_id' not in res or not res.get('violation_id'):
            ctx_violation = self._context.get('default_violation_id')
            if ctx_violation:
                res['violation_id'] = ctx_violation
        return res

    @api.model_create_multi
    def create(self, vals_list):
        """
        Safety net: neu violation_id van chua duoc set (do readonly field
        khong duoc submit), lay tu context.
        """
        ctx_violation = self._context.get('default_violation_id')
        for vals in vals_list:
            if not vals.get('violation_id') and ctx_violation:
                vals['violation_id'] = ctx_violation
        return super().create(vals_list)

    @api.constrains('violation_id')
    def _check_unique_explanation(self):
        for rec in self:
            dup = self.search([
                ('violation_id', '=', rec.violation_id.id),
                ('id', '!=', rec.id),
            ])
            if dup:
                raise ValidationError(
                    'Vi pham nay da co giai trinh. Vui long sua giai trinh hien tai.'
                )

    def action_submit(self):
        self.ensure_one()

        # Safety: neu violation_id van trong (truong hop hiem), thu lay tu context
        if not self.violation_id:
            ctx_violation = self._context.get('default_violation_id')
            if ctx_violation:
                self.write({'violation_id': ctx_violation})
            else:
                raise UserError(
                    'Khong xac dinh duoc vi pham lien quan. '
                    'Vui long mo giai trinh tu tab "Giai trinh" tren form Vi pham.'
                )

        if not self.explanation_text or not self.explanation_text.strip():
            raise UserError('Vui long nhap noi dung giai trinh truoc khi gui.')
        if self.is_overdue:
            raise UserError('Da qua han giai trinh. Khong the gui.')

        self.write({
            'state': 'submitted',
            'submitted_at': fields.Datetime.now(),
        })
        self.violation_id.write({'state': 'explained'})

        manager = self.employee_id.parent_id
        if manager and manager.user_id:
            self.violation_id.activity_schedule(
                'mail.mail_activity_data_todo',
                note=(
                    f'Nhan vien {self.employee_id.name} da gui giai trinh '
                    f'vi pham ngay {self.violation_id.date}.'
                ),
                user_id=manager.user_id.id,
                date_deadline=fields.Date.today() + timedelta(days=1),
            )

    def action_accept(self):
        self.ensure_one()
        self.write({
            'state': 'accepted',
            'reviewed_by_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(),
        })
        self.violation_id.action_waive()
        if self.employee_id.user_id:
            self.message_post(
                body='<p>Giai trinh cua ban da duoc <b>chap nhan</b>. Vi pham khong bi tru luong.</p>',
                partner_ids=[self.employee_id.user_id.partner_id.id],
            )

    def action_reject(self):
        self.ensure_one()
        if not self.manager_note or not self.manager_note.strip():
            raise UserError('Vui long nhap ly do tu choi vao "Ghi chu cua Manager".')
        self.write({
            'state': 'rejected',
            'reviewed_by_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(),
        })
        self.violation_id.action_confirm()
        if self.employee_id.user_id:
            self.message_post(
                body=(
                    f'<p>Giai trinh cua ban da bi <b>tu choi</b>.</p>'
                    f'<p><b>Ly do:</b> {self.manager_note}</p>'
                    f'<p>Vi pham se duoc tinh vao khau tru luong thang nay.</p>'
                ),
                partner_ids=[self.employee_id.user_id.partner_id.id],
            )

    @api.model
    def _cron_auto_confirm_expired(self):
        now = fields.Datetime.now()
        expired_violations = self.env['hr.attendance.violation'].search([
            ('state', 'in', ['detected', 'pending_explanation']),
        ])
        for violation in expired_violations:
            rule = violation.rule_id
            if not rule or not rule.requires_explanation:
                violation.action_confirm()
                continue
            deadline_hours = rule.explanation_deadline_hours or 48
            detection_time = violation.create_date or now
            deadline = detection_time + timedelta(hours=deadline_hours)
            if now > deadline:
                existing = self.search([('violation_id', '=', violation.id)])
                if not existing:
                    violation.action_confirm()
                    violation.message_post(
                        body=(
                            f'Vi pham tu dong xac nhan do het han giai trinh '
                            f'({deadline.strftime("%d/%m/%Y %H:%M")}).'
                        )
                    )
