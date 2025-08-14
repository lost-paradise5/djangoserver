from django.db import models

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
