from django import forms
from django.core.exceptions import ValidationError

from .models import Request


class RequestForm(forms.ModelForm):
    """
    Форма заявки на новую позицию (ADR-007/ADR-012).

    `shown_candidate_ids` — служебное скрытое поле: id похожих позиций,
    которые фронтенд показал конструктору по итогам живого поиска (см.
    similar_nomenclature в views.py). ADR-007 требует, чтобы каждая
    показанная позиция была отмечена — «да, это оно» (тогда её id попадёт в
    similar_to) либо «просмотрел, не подходит» (reviewed_not_matching).
    Ничего не показали — шаг неприменим, форма проходит без него.

    Важно: это проверка внутренней согласованности того, что клиент
    заявляет как «показанное» (shown_candidate_ids приходит от JS и ничем
    не подтверждается сервером) — а не независимая перепроверка похожести.
    Ничего не мешает клиенту не подтянуть список похожих вовсе и отправить
    shown_candidate_ids пустым, даже если явные дубли есть. Для доверенных
    сотрудников внутри организации это приемлемый компромисс (тот же дух,
    что и выбор живого JS-поиска вместо принудительной двухшаговой формы),
    но не путать с гарантией отсутствия дублей — конечная защита от дублей
    остаётся на Владимире при одобрении заявки.
    """

    shown_candidate_ids = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Единое оформление полей дизайн-системой Prostor — служебные скрытые
        # поля не трогаем.
        for field in self.fields.values():
            if isinstance(
                field.widget, (forms.HiddenInput, forms.MultipleHiddenInput)
            ):
                continue
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = f'{existing} prostor-control'.strip()

        self.fields['guessed_kind'].empty_label = 'Не определено'

    class Meta:
        model = Request
        fields = [
            'description',
            'guessed_kind',
            'unit',
            'gost_or_article',
            'analog_url',
            'similar_to',
            'reviewed_not_matching',
        ]
        widgets = {
            'similar_to': forms.HiddenInput(),
            'reviewed_not_matching': forms.MultipleHiddenInput(),
        }

    def clean(self):
        cleaned = super().clean()

        shown_ids = {
            int(raw) for raw in cleaned.get('shown_candidate_ids', '').split(',')
            if raw.strip().isdigit()
        }
        if not shown_ids:
            return cleaned

        reviewed_ids = {
            nomenclature.id
            for nomenclature in cleaned.get('reviewed_not_matching') or []
        }
        similar_to = cleaned.get('similar_to')
        if similar_to:
            reviewed_ids.add(similar_to.id)

        if shown_ids - reviewed_ids:
            raise ValidationError(
                'Отметьте каждую похожую позицию из списка: «да, это оно» '
                'либо «просмотрел, не подходит».'
            )

        return cleaned
