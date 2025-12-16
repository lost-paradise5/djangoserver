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
