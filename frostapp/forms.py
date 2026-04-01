from django import forms

from .models import (
    MaxBotRole,
    MaxBotEmployee,
    MaxBotVehicle,
    MaxBotScenario,
    MaxBotQuestion,
    MaxBotQuestionOption,
)


QUESTION_TYPE_CHOICES = [
    ("single_choice", "Кнопки / один вариант"),
    ("text", "Текстовый ответ"),
    ("number", "Числовой ответ"),
    ("photo", "Фото / несколько фото"),
]

REQUEST_STATUS_ACTION_CHOICES = [
    ("", "Обычное продолжение"),
    ("emergency_stop", "Аварийный СТОП"),
]


class StyledModelForm(forms.ModelForm):
    """
    Базовая форма с оформлением полей.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for name, field in self.fields.items():
            widget = field.widget

            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
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
        labels = {
            "code": "Служебный код",
            "name": "Название должности",
            "requires_employee": "Автоматически определять сотрудника",
            "requires_vehicle": "Показывать выбор машины",
            "is_active": "Активна",
            "sort_order": "Порядок сортировки",
        }
        help_texts = {
            "code": "Внутренний код роли. Лучше короткий и уникальный.",
            "name": "Как эта должность будет называться в боте.",
            "requires_employee": "Если включено, бот сам определит сотрудника по user / ФИО / телефону, без выбора ФИО в чате.",
            "requires_vehicle": "Если включено, после выбора должности бот предложит выбрать машину.",
        }


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
        labels = {
            "role": "Должность",
            "user": "Пользователь из users",
            "full_name": "ФИО",
            "phone": "Телефон",
            "is_active": "Активен",
            "sort_order": "Порядок сортировки",
        }
        help_texts = {
            "user": "Главная связь для автоопределения сотрудника ботом.",
            "full_name": "Используется в логах и как резервный вариант сопоставления.",
            "phone": "Резервный вариант сопоставления, если user не указан.",
        }


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
        labels = {
            "role": "Должность",
            "employee": "Закреплённый сотрудник",
            "reg_number": "Номер машины",
            "title": "Название / описание",
            "is_active": "Активна",
            "sort_order": "Порядок сортировки",
        }
        help_texts = {
            "employee": "Необязательно. Если указать, машина будет показываться только этому сотруднику.",
            "reg_number": "Госномер или внутренний номер.",
            "title": "Например: Газель 12, Погрузчик 3, Фура 7.",
        }


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
        labels = {
            "role": "Для какой должности",
            "code": "Служебный код сценария",
            "name": "Название сценария",
            "is_active": "Активен",
            "first_question": "Первый вопрос сценария",
        }
        help_texts = {
            "code": "Уникальный код сценария внутри одной должности.",
            "name": "Понятное название сценария для интерфейса.",
            "first_question": "Именно с этого вопроса бот начнёт сценарий.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            self.fields["first_question"].queryset = instance.questions.order_by("sort_order", "id")
        else:
            self.fields["first_question"].queryset = MaxBotQuestion.objects.none()


class MaxBotQuestionForm(StyledModelForm):
    question_type = forms.ChoiceField(
        choices=QUESTION_TYPE_CHOICES,
        label="Тип ответа"
    )

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
        labels = {
            "code": "Служебный код вопроса",
            "text": "Текст вопроса",
            "is_required": "Обязательный вопрос",
            "is_active": "Активен",
            "sort_order": "Порядок",
            "placeholder": "Подсказка в поле",
            "help_text": "Дополнительная подсказка",
            "default_next_question": "Следующий вопрос по умолчанию",
        }
        help_texts = {
            "text": "Именно этот текст увидит пользователь в MAX.",
            "placeholder": "Актуально в основном для текстовых и числовых ответов.",
            "help_text": "Можно пояснить, что именно нужно отправить.",
            "default_next_question": "Для текстовых, числовых и фото-вопросов переход обычно идёт сюда.",
        }

    def __init__(self, *args, **kwargs):
        self.scenario = kwargs.pop("scenario", None)
        super().__init__(*args, **kwargs)

        instance = getattr(self, "instance", None)
        scenario = self.scenario or (instance.scenario if instance and instance.pk else None)

        if scenario:
            qs = scenario.questions.order_by("sort_order", "id")
            if instance and instance.pk:
                qs = qs.exclude(pk=instance.pk)
            self.fields["default_next_question"].queryset = qs
        else:
            self.fields["default_next_question"].queryset = MaxBotQuestion.objects.none()


class MaxBotQuestionOptionForm(StyledModelForm):
    request_status_on_select = forms.ChoiceField(
        choices=REQUEST_STATUS_ACTION_CHOICES,
        required=False,
        label="Что сделать со статусом заявки"
    )

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
        labels = {
            "code": "Служебный код варианта",
            "text": "Текст кнопки",
            "numeric_value": "Числовое значение",
            "registry_action_text": "Что записать в итог / реестр",
            "notification_text": "Короткое уведомление пользователю",
            "request_status_on_select": "Статус после выбора",
            "is_emergency": "Аварийный вариант",
            "is_finish": "Завершает сценарий",
            "is_active": "Активен",
            "sort_order": "Порядок",
            "next_question": "Следующий вопрос",
        }
        help_texts = {
            "text": "Именно этот текст увидит пользователь на кнопке. В пределах одного вопроса тексты лучше не дублировать.",
            "numeric_value": "Необязательно. Можно использовать для числовой фиксации ответа.",
            "registry_action_text": "Попадёт в итог заявки и в реестр.",
            "notification_text": "Необязательно. Короткое сообщение, которое бот покажет после выбора.",
            "request_status_on_select": "Например, можно сразу перевести заявку в аварийный СТОП.",
            "is_emergency": "Если включено, шаг будет считаться аварийным.",
            "is_finish": "Если включено и нет следующего вопроса, сценарий завершится.",
        }

    def __init__(self, *args, **kwargs):
        self.question = kwargs.pop("question", None)
        super().__init__(*args, **kwargs)

        instance = getattr(self, "instance", None)
        question = self.question or (instance.question if instance and instance.pk else None)

        if question:
            self.fields["next_question"].queryset = question.scenario.questions.order_by("sort_order", "id")
        else:
            self.fields["next_question"].queryset = MaxBotQuestion.objects.none()
