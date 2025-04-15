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
