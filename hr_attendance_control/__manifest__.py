{
    'name': 'HR Attendance Control — Kiểm soát vi phạm chấm công',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Attendances',
    'summary': (
        'Tự động phát hiện vi phạm chấm công, '
        'workflow giải trình, tích hợp Payroll'
    ),
    'author': 'ThuyTrang',
    'depends': [
        'hr',
        'hr_attendance',
        'hr_holidays',
        'mail',
        'resource',
        'payroll',        
    ],
    'data': [
        'security/hr_attendance_control_security.xml',
        'security/ir.model.access.csv',
        'data/mail_activity_data.xml',
        'data/violation_rule_data.xml',
        'data/ir_cron_data.xml',
        'views/hr_attendance_explanation_views.xml',
        'views/hr_attendance_rule_views.xml',
        'views/hr_attendance_violation_views.xml',
        'views/hr_attendance_penalty_views.xml',
        'views/hr_attendance_views_inherit.xml',
        'views/hr_employee_views.xml',
        'wizard/violation_report_wizard_views.xml',
        'views/menu_items.xml',
        'report/violation_report_template.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}