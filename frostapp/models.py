from django.db import models
import uuid

class Queue(models.Model):
    id = models.AutoField(primary_key=True)
    data = models.JSONField() 

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    attempts = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_attempt = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'queue'
        managed = False
        
class MODUL_logs(models.Model):
    id = models.AutoField(primary_key=True)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'modul_logs'
        managed = False  


        
        
class Logs(models.Model):
    id = models.AutoField(primary_key=True)
    location = models.CharField(max_length=10)  # 'INTERFACE' или 'MODUL'
    interface_id = models.IntegerField(null=True, blank=True)
    modul_id = models.IntegerField(null=True, blank=True)
    action = models.CharField(max_length=10)  # 'CREATE','UPDATE','DELETE'
    timestamp = models.DateTimeField(auto_now_add=True)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'logs'
        managed = False  # поскольку таблицу создали вручную SQL














class User(models.Model):
    id = models.AutoField(primary_key=True)
    employee_id = models.CharField(max_length=20)
    max_id = models.BigIntegerField(null=True, blank=True)
    encrypted_inn = models.CharField(max_length=128)
    full_name = models.CharField(max_length=255)
    mail = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    department_id = models.IntegerField()
    position_id = models.IntegerField()
    active = models.BooleanField(default=True)
    tg_status = models.BooleanField(default=False)
    tg_id = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = 'users'
        managed = False


class UKMUser(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    roleid = models.IntegerField()
    storeid = models.IntegerField()
    version = models.IntegerField()

    class Meta:
        db_table = 'ukm_users'
        managed = False


class OpenInSystem(models.Model):
    id        = models.AutoField(primary_key=True)
    user_id   = models.IntegerField(db_column='user_id')
    username = models.CharField(max_length=255)
    password = models.CharField(max_length=128)
    system_id = models.IntegerField()
    status = models.BooleanField()
    isnot2fa = models.BooleanField(db_column='isnot2fa', null=True, blank=True, default=False)

    class Meta:
        db_table = 'open_in_system'
        managed = False


class QRCode(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column='user_id')
    qr_data = models.TextField()
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'qr_code'
        managed = False





class Department(models.Model):
    id   = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)

    class Meta:
        db_table = 'departments'
        managed  = False


class Position(models.Model):
    id   = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)

    class Meta:
        db_table = 'positions'
        managed  = False


class Store(models.Model):
    """
    Таблица магазинов из PostgreSQL:
    stores(id, region, smstore, name, address, close_date,
           ukm4store, ukm4ip, ukm5store, latitude, longitude)
    """
    id = models.AutoField(primary_key=True)
    region = models.TextField(null=True, blank=True)
    smstore = models.IntegerField(null=True, blank=True)
    name = models.TextField(null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    close_date = models.DateField(null=True, blank=True)

    ukm4store = models.IntegerField(null=True, blank=True)
    ukm4ip = models.GenericIPAddressField(null=True, blank=True)
    ukm5store = models.IntegerField(null=True, blank=True)

    dbname = models.TextField(null=True, blank=True)

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        db_table = 'stores'
        managed = False



class AuthSession(models.Model):
    STATUS_CHOICES = [
        ('pending',   'Pending'),   # телефон подтверждён, магазин ещё не выбран
        ('pin_sent',  'PIN sent'),  # PIN отправлен, ждём ввода
        ('success',   'Success'),   # PIN успешно введён
        ('expired',   'Expired'),   # истёк срок действия
        ('blocked',   'Blocked'),   # превышено количество попыток
    ]

    id = models.BigAutoField(primary_key=True)
    session_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    storeid = models.IntegerField(null=True, blank=True)  # ukm_users.storeid (ukm4store)
    pin_hash = models.CharField(max_length=64, null=True, blank=True)  # SHA-256(PIN)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    attempts = models.IntegerField(default=0)              # сколько неверных попыток уже было
    expires_at = models.DateTimeField()                    # до какого момента жива сессия/ПИН
    created_at = models.DateTimeField(auto_now_add=True)   # совпадает с DEFAULT NOW()
    updated_at = models.DateTimeField(auto_now=True)       # будем обновлять через save()

    class Meta:
        db_table = 'auth_sessions'
        managed = False 




class QRIssueLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)

    endpoint = models.CharField(max_length=64)
    method   = models.CharField(max_length=32)
    status   = models.CharField(max_length=16)

    user = models.ForeignKey(
        'User',
        models.DO_NOTHING,
        db_column='user_id',
        blank=True,
        null=True,
    )
    employee_inn = models.CharField(max_length=20, blank=True, null=True)
    employee_fio = models.TextField(blank=True, null=True)
    tg_id        = models.CharField(max_length=32, blank=True, null=True)

    phone_raw        = models.CharField(max_length=32, blank=True, null=True)
    phone_normalized = models.CharField(max_length=32, blank=True, null=True)

    sm_store_id  = models.IntegerField(blank=True, null=True)
    ukm_store_id = models.IntegerField(blank=True, null=True)
    role_id      = models.IntegerField(blank=True, null=True)

    qr_data       = models.TextField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    raw_request   = models.JSONField(blank=True, null=True)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'qr_issue_logs'


class VpnAccessSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    status = models.CharField(max_length=16) 
    ad_login = models.CharField(max_length=128)
    ad_user_dn = models.TextField(null=True, blank=True)
    inn = models.CharField(max_length=12, null=True, blank=True)
    bitrix_user_id = models.IntegerField(null=True, blank=True)
    head_department_ids = models.JSONField(null=True, blank=True)

    pin_salt = models.CharField(max_length=32)
    pin_hash = models.CharField(max_length=64)
    pin_attempts = models.IntegerField(default=0)

    created_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)

    ip = models.CharField(max_length=64, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "vpn_access_sessions"






























class AdminBadgeRequest(models.Model):
    STATUS_CHOICES = [
        ("NEW", "NEW"),
        ("STORE_SELECTED", "STORE_SELECTED"),
        ("PENDING_ADMIN", "PENDING_ADMIN"),
        ("ACCEPTED", "ACCEPTED"),
        ("REJECTED", "REJECTED"),
        ("EXPIRED", "EXPIRED"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="NEW")

    cashier_user_id = models.IntegerField()
    cashier_tg_id = models.CharField(max_length=64)
    cashier_full_name = models.CharField(max_length=255, blank=True, default="")

    store_ids = models.JSONField(null=True, blank=True)   # jsonb
    storeid = models.IntegerField(null=True, blank=True)

    admin_user_id = models.IntegerField(null=True, blank=True)
    admin_tg_id = models.CharField(max_length=64, null=True, blank=True)
    admin_full_name = models.CharField(max_length=255, null=True, blank=True)

    decision = models.CharField(max_length=16, null=True, blank=True) 
    decided_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    ip = models.CharField(max_length=64, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    meta = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "admin_badge_requests"
        managed = False  
























class VpnAccessBaseline(models.Model):
    id = models.BigAutoField(primary_key=True)
    inn = models.CharField(max_length=12, unique=True)
    ad_user_dn = models.TextField(null=True, blank=True)
    ad_login = models.CharField(max_length=128, null=True, blank=True)
    baseline_member = models.BooleanField(default=False)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "vpn_access_baselines"
        managed = False


class VpnAccessLease(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)

    lease_type = models.CharField(max_length=8)
    inn = models.CharField(max_length=12)

    target_bitrix_user_id = models.IntegerField(null=True, blank=True)
    created_by_ad_login = models.CharField(max_length=128, null=True, blank=True)
    created_by_bitrix_user_id = models.IntegerField(null=True, blank=True)

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=16, default="ACTIVE")  
    created_at = models.DateTimeField()
    notify_sent_at = models.DateTimeField(null=True, blank=True)

    meta = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "vpn_access_leases"
        managed = False












class MaxBotRole(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    requires_employee = models.BooleanField(default=True)
    requires_vehicle = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_role"
        managed = False
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class MaxBotEmployee(models.Model):
    id = models.BigAutoField(primary_key=True)
    role = models.ForeignKey(
        MaxBotRole,
        db_column="role_id",
        on_delete=models.CASCADE,
        related_name="employees",
    )
    user = models.ForeignKey(
        User,
        db_column="user_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maxbot_directory_rows",
    )
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_employee"
        managed = False
        ordering = ["sort_order", "full_name"]

    def __str__(self):
        return self.full_name


class MaxBotVehicle(models.Model):
    id = models.BigAutoField(primary_key=True)
    role = models.ForeignKey(
        MaxBotRole,
        db_column="role_id",
        on_delete=models.CASCADE,
        related_name="vehicles",
    )
    employee = models.ForeignKey(
        MaxBotEmployee,
        db_column="employee_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicles",
    )
    reg_number = models.CharField(max_length=64)
    title = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_vehicle"
        managed = False
        ordering = ["sort_order", "reg_number"]

    def __str__(self):
        return self.title or self.reg_number


class MaxBotScenario(models.Model):
    id = models.BigAutoField(primary_key=True)
    role = models.ForeignKey(
        MaxBotRole,
        db_column="role_id",
        on_delete=models.CASCADE,
        related_name="scenarios",
    )
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    first_question = models.ForeignKey(
        "MaxBotQuestion",
        db_column="first_question_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_scenario"
        managed = False
        ordering = ["name"]
        unique_together = ("role", "code")

    def __str__(self):
        return f"{self.role.name} / {self.name}"


class MaxBotQuestion(models.Model):
    id = models.BigAutoField(primary_key=True)
    scenario = models.ForeignKey(
        MaxBotScenario,
        db_column="scenario_id",
        on_delete=models.CASCADE,
        related_name="questions",
    )
    code = models.CharField(max_length=64)
    text = models.TextField()
    question_type = models.CharField(max_length=32)
    is_required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    placeholder = models.TextField(null=True, blank=True)
    help_text = models.TextField(null=True, blank=True)
    default_next_question = models.ForeignKey(
        "self",
        db_column="default_next_question_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_prev_questions",
    )
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_question"
        managed = False
        ordering = ["sort_order", "id"]
        unique_together = ("scenario", "code")

    def __str__(self):
        return f"{self.scenario.name}: {self.text[:60]}"


class MaxBotQuestionOption(models.Model):
    id = models.BigAutoField(primary_key=True)
    question = models.ForeignKey(
        MaxBotQuestion,
        db_column="question_id",
        on_delete=models.CASCADE,
        related_name="options",
    )
    code = models.CharField(max_length=64)
    text = models.CharField(max_length=255)
    numeric_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    registry_action_text = models.TextField(null=True, blank=True)
    notification_text = models.TextField(null=True, blank=True)
    request_status_on_select = models.CharField(max_length=32, null=True, blank=True)
    is_emergency = models.BooleanField(default=False)
    is_finish = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    next_question = models.ForeignKey(
        MaxBotQuestion,
        db_column="next_question_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_options",
    )
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_question_option"
        managed = False
        ordering = ["sort_order", "id"]
        unique_together = ("question", "code")

    def __str__(self):
        return self.text


class MaxBotRequest(models.Model):
    id = models.BigAutoField(primary_key=True)
    request_no = models.CharField(max_length=64, unique=True)
    applicant_user = models.ForeignKey(
        User,
        db_column="applicant_user_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maxbot_requests",
    )
    applicant_full_name = models.CharField(max_length=255)
    max_user_id = models.BigIntegerField()
    max_chat_id = models.BigIntegerField(null=True, blank=True)
    role = models.ForeignKey(
        MaxBotRole,
        db_column="role_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests",
    )
    employee = models.ForeignKey(
        MaxBotEmployee,
        db_column="employee_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests",
    )
    vehicle = models.ForeignKey(
        MaxBotVehicle,
        db_column="vehicle_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests",
    )
    scenario = models.ForeignKey(
        MaxBotScenario,
        db_column="scenario_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests",
    )
    current_question = models.ForeignKey(
        MaxBotQuestion,
        db_column="current_question_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_requests",
    )
    status = models.CharField(max_length=32, default="draft")
    emergency_flag = models.BooleanField(default=False)
    summary = models.TextField(null=True, blank=True)
    raw_last_event = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "maxbot_request"
        managed = False
        ordering = ["-created_at"]

    def __str__(self):
        return self.request_no


class MaxBotRequestAnswer(models.Model):
    id = models.BigAutoField(primary_key=True)
    request = models.ForeignKey(
        MaxBotRequest,
        db_column="request_id",
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(
        MaxBotQuestion,
        db_column="question_id",
        on_delete=models.CASCADE,
        related_name="request_answers",
    )
    option = models.ForeignKey(
        MaxBotQuestionOption,
        db_column="option_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="request_answers",
    )
    question_text = models.TextField()
    option_text = models.CharField(max_length=255, null=True, blank=True)
    answer_text = models.TextField(null=True, blank=True)
    answer_number = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    photo_url = models.TextField(null=True, blank=True)
    photo_file = models.FileField(upload_to="maxbot_photos/%Y/%m/%d/", null=True, blank=True)
    registry_action_text = models.TextField(null=True, blank=True)
    is_emergency = models.BooleanField(default=False)
    raw_payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_request_answer"
        managed = False
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.request.request_no} / {self.question_text[:50]}"





























#НОВЫЙ_БОТ




class MaxBotChecklistDepartment(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_checklist_department"
        managed = False
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class MaxBotChecklistDepartmentAccess(models.Model):
    id = models.BigAutoField(primary_key=True)
    checklist_department = models.ForeignKey(
        MaxBotChecklistDepartment,
        db_column="checklist_department_id",
        on_delete=models.CASCADE,
        related_name="access_rows",
    )
    user_department_id = models.IntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_checklist_department_access"
        managed = False
        unique_together = ("checklist_department", "user_department_id")


class MaxBotChecklistLocation(models.Model):
    id = models.BigAutoField(primary_key=True)
    checklist_department = models.ForeignKey(
        MaxBotChecklistDepartment,
        db_column="checklist_department_id",
        on_delete=models.CASCADE,
        related_name="locations",
    )
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_checklist_location"
        managed = False
        ordering = ["sort_order", "name"]
        unique_together = ("checklist_department", "name")

    def __str__(self):
        return self.name


class MaxBotChecklistWorkplace(models.Model):
    id = models.BigAutoField(primary_key=True)
    location = models.ForeignKey(
        MaxBotChecklistLocation,
        db_column="location_id",
        on_delete=models.CASCADE,
        related_name="workplaces",
    )
    registry_no = models.CharField(max_length=32, null=True, blank=True)
    name = models.CharField(max_length=255)
    responsible_name = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_checklist_workplace"
        managed = False
        ordering = ["sort_order", "name"]
        unique_together = ("location", "name")

    def __str__(self):
        return self.name


class MaxBotChecklistQuestion(models.Model):
    id = models.BigAutoField(primary_key=True)
    step_code = models.CharField(max_length=64)
    step_name = models.CharField(max_length=255)
    question_no = models.IntegerField()
    text = models.TextField()
    yes_score = models.IntegerField(default=1)
    no_score = models.IntegerField(default=0)
    photo_required_if_no = models.BooleanField(default=False)
    photo_required_if_yes = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_checklist_question"
        managed = False
        ordering = ["sort_order", "id"]
        unique_together = ("step_code", "question_no")

    def __str__(self):
        return f"{self.step_name} / {self.question_no}"


class MaxBotChecklistSession(models.Model):
    id = models.BigAutoField(primary_key=True)
    session_no = models.CharField(max_length=64, unique=True)
    applicant_user = models.ForeignKey(
        User,
        db_column="applicant_user_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maxbot_checklist_sessions",
    )
    applicant_full_name = models.CharField(max_length=255)
    max_user_id = models.BigIntegerField()
    max_chat_id = models.BigIntegerField(null=True, blank=True)

    checklist_department = models.ForeignKey(
        MaxBotChecklistDepartment,
        db_column="checklist_department_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )
    location = models.ForeignKey(
        MaxBotChecklistLocation,
        db_column="location_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )
    workplace = models.ForeignKey(
        MaxBotChecklistWorkplace,
        db_column="workplace_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )

    department_name = models.CharField(max_length=255, blank=True, default="")
    location_name = models.CharField(max_length=255, blank=True, default="")
    workplace_name = models.CharField(max_length=255, blank=True, default="")
    responsible_name = models.CharField(max_length=255, null=True, blank=True)

    current_question = models.ForeignKey(
        MaxBotChecklistQuestion,
        db_column="current_question_id",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_sessions",
    )

    status = models.CharField(max_length=32, default="awaiting_department")
    total_score = models.IntegerField(default=0)
    summary = models.TextField(null=True, blank=True)
    report_dir = models.TextField(null=True, blank=True)
    report_file = models.TextField(null=True, blank=True)
    raw_last_event = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "maxbot_checklist_session"
        managed = False
        ordering = ["-created_at"]

    def __str__(self):
        return self.session_no


class MaxBotChecklistAnswer(models.Model):
    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(
        MaxBotChecklistSession,
        db_column="session_id",
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(
        MaxBotChecklistQuestion,
        db_column="question_id",
        on_delete=models.CASCADE,
        related_name="answers",
    )
    step_code = models.CharField(max_length=64)
    step_name = models.CharField(max_length=255)
    question_no = models.IntegerField()
    question_text = models.TextField()
    answer_value = models.BooleanField()
    answer_label = models.CharField(max_length=16)
    score = models.IntegerField(default=0)
    photo_required = models.BooleanField(default=False)
    raw_payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_checklist_answer"
        managed = False
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.session.session_no} / {self.step_name} / {self.question_no}"


class MaxBotChecklistAnswerPhoto(models.Model):
    id = models.BigAutoField(primary_key=True)
    answer = models.ForeignKey(
        MaxBotChecklistAnswer,
        db_column="answer_id",
        on_delete=models.CASCADE,
        related_name="photos",
    )
    photo_url = models.TextField(null=True, blank=True)
    photo_file = models.CharField(max_length=512, null=True, blank=True)
    original_name = models.CharField(max_length=255, null=True, blank=True)
    saved_name = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "maxbot_checklist_answer_photo"
        managed = False
        ordering = ["created_at", "id"]





