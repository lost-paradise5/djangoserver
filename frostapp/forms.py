from django import forms

from .models import (
    MaxBotRole,
    MaxBotEmployee,
    MaxBotVehicle,
    MaxBotScenario,
    MaxBotQuestion,
    MaxBotQuestionOption,
)


class StyledModelForm(forms.ModelForm):
    """
    Базовая форма с простым оформлением полей.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "form-check-input"
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            elif isinstance(widget, forms.Textarea):
                widget.attrs["class"] = "form-control"
                widget.attrs.setdefault("rows", 3)
            else:
                widget.attrs["class"] = "form-control"


class MaxBotRoleForm(StyledModelForm):
    class Meta:
        model = MaxBotRole
        fields = [
            "code",
            "name",
            "requires_employee",
            "requires_vehicle",
            "is_active",
            "sort_order",
        ]


class MaxBotEmployeeForm(StyledModelForm):
    class Meta:
        model = MaxBotEmployee
        fields = [
            "role",
            "user",
            "full_name",
            "phone",
            "is_active",
            "sort_order",
        ]


class MaxBotVehicleForm(StyledModelForm):
    class Meta:
        model = MaxBotVehicle
        fields = [
            "role",
            "employee",
            "reg_number",
            "title",
            "is_active",
            "sort_order",
        ]


class MaxBotScenarioForm(StyledModelForm):
    class Meta:
        model = MaxBotScenario
        fields = [
            "role",
            "code",
            "name",
            "is_active",
            "first_question",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            self.fields["first_question"].queryset = instance.questions.order_by("sort_order", "id")
        else:
            self.fields["first_question"].queryset = MaxBotQuestion.objects.none()


class MaxBotQuestionForm(StyledModelForm):
    class Meta:
        model = MaxBotQuestion
        fields = [
            "code",
            "text",
            "question_type",
            "is_required",
            "is_active",
            "sort_order",
            "placeholder",
            "help_text",
            "default_next_question",
        ]

    def __init__(self, *args, **kwargs):
        self.scenario = kwargs.pop("scenario", None)
        super().__init__(*args, **kwargs)

        instance = getattr(self, "instance", None)
        scenario = self.scenario or (instance.scenario if instance and instance.pk else None)

        if scenario:
            self.fields["default_next_question"].queryset = scenario.questions.order_by("sort_order", "id")
        else:
            self.fields["default_next_question"].queryset = MaxBotQuestion.objects.none()


class MaxBotQuestionOptionForm(StyledModelForm):
    class Meta:
        model = MaxBotQuestionOption
        fields = [
            "code",
            "text",
            "numeric_value",
            "registry_action_text",
            "notification_text",
            "request_status_on_select",
            "is_emergency",
            "is_finish",
            "is_active",
            "sort_order",
            "next_question",
        ]

    def __init__(self, *args, **kwargs):
        self.question = kwargs.pop("question", None)
        super().__init__(*args, **kwargs)

        instance = getattr(self, "instance", None)
        question = self.question or (instance.question if instance and instance.pk else None)

        if question:
            self.fields["next_question"].queryset = question.scenario.questions.order_by("sort_order", "id")
        else:
            self.fields["next_question"].queryset = MaxBotQuestion.objects.none()
