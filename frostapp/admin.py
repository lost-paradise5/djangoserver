from django.contrib import admin
from django.utils.html import format_html

from .models import (
    MaxBotRole,
    MaxBotEmployee,
    MaxBotVehicle,
    MaxBotScenario,
    MaxBotQuestion,
    MaxBotQuestionOption,
    MaxBotRequest,
    MaxBotRequestAnswer,
)


class MaxBotQuestionOptionInline(admin.TabularInline):
    model = MaxBotQuestionOption
    extra = 0
    fields = (
        "code",
        "text",
        "numeric_value",
        "registry_action_text",
        "notification_text",
        "request_status_on_select",
        "is_emergency",
        "is_finish",
        "next_question",
        "sort_order",
        "is_active",
    )


@admin.register(MaxBotQuestion)
class MaxBotQuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "scenario", "code", "question_type", "sort_order", "is_active")
    list_filter = ("scenario", "question_type", "is_active")
    search_fields = ("code", "text")
    ordering = ("scenario", "sort_order", "id")
    inlines = [MaxBotQuestionOptionInline]


@admin.register(MaxBotRole)
class MaxBotRoleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "requires_employee", "requires_vehicle", "is_active", "sort_order")
    list_filter = ("is_active", "requires_employee", "requires_vehicle")
    search_fields = ("name", "code")
    ordering = ("sort_order", "name")


@admin.register(MaxBotEmployee)
class MaxBotEmployeeAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "role", "phone", "user", "is_active", "sort_order")
    list_filter = ("role", "is_active")
    search_fields = ("full_name", "phone")
    ordering = ("role", "sort_order", "full_name")


@admin.register(MaxBotVehicle)
class MaxBotVehicleAdmin(admin.ModelAdmin):
    list_display = ("id", "reg_number", "title", "role", "employee", "is_active", "sort_order")
    list_filter = ("role", "is_active")
    search_fields = ("reg_number", "title")
    ordering = ("role", "sort_order", "reg_number")


@admin.register(MaxBotScenario)
class MaxBotScenarioAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "role", "is_active", "first_question")
    list_filter = ("role", "is_active")
    search_fields = ("name", "code")
    ordering = ("role", "name")


class MaxBotRequestAnswerInline(admin.TabularInline):
    model = MaxBotRequestAnswer
    extra = 0
    can_delete = False
    readonly_fields = (
        "question",
        "question_text",
        "option",
        "option_text",
        "answer_text",
        "answer_number",
        "registry_action_text",
        "is_emergency",
        "created_at",
        "photo_preview",
        "photo_url",
        "photo_file",
    )
    fields = (
        "question",
        "question_text",
        "option",
        "option_text",
        "answer_text",
        "answer_number",
        "registry_action_text",
        "is_emergency",
        "created_at",
        "photo_preview",
        "photo_url",
        "photo_file",
    )

    def photo_preview(self, obj):
        if obj.photo_file:
            return format_html('<a href="{}" target="_blank">Открыть фото</a>', obj.photo_file.url)
        if obj.photo_url:
            return format_html('<a href="{}" target="_blank">Открыть исходный URL</a>', obj.photo_url)
        return "-"
    photo_preview.short_description = "Фото"


@admin.register(MaxBotRequest)
class MaxBotRequestAdmin(admin.ModelAdmin):
    list_display = (
        "request_no",
        "applicant_full_name",
        "role",
        "employee",
        "vehicle",
        "status",
        "emergency_flag",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "emergency_flag", "role", "created_at")
    search_fields = (
        "request_no",
        "applicant_full_name",
        "summary",
        "employee__full_name",
        "vehicle__reg_number",
    )
    readonly_fields = ("created_at", "updated_at", "finished_at", "summary")
    ordering = ("-created_at",)
    inlines = [MaxBotRequestAnswerInline]


@admin.register(MaxBotRequestAnswer)
class MaxBotRequestAnswerAdmin(admin.ModelAdmin):
    list_display = ("id", "request", "question", "option_text", "answer_text", "answer_number", "is_emergency", "created_at")
    list_filter = ("is_emergency", "created_at", "question")
    search_fields = ("request__request_no", "question_text", "option_text", "answer_text")
    ordering = ("-created_at",)
