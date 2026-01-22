from django import template

register = template.Library()

@register.filter(name='split')
def split(value, arg=','):
    """Разбивает строку по указанному разделителю."""
    return value.split(arg)
