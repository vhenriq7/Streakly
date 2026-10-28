import sqlite3

import flet as ft

from storage import add_subject, initialize_database, list_subjects


def main(page: ft.Page) -> None:
    page.title = "Streakly — prova de conceito"
    page.padding = 24

    name_field = ft.TextField(label="Nome da matéria", hint_text="Ex.: Python")
    message = ft.Text()
    subjects = ft.ListView(expand=True, spacing=8)

    def refresh_subjects() -> None:
        subjects.controls = [ft.Text(name) for name in list_subjects()]
        if not subjects.controls:
            subjects.controls = [ft.Text("Nenhuma matéria cadastrada ainda.")]

    def save_subject(event: ft.ControlEvent) -> None:
        name = name_field.value.strip()
        if not name:
            message.value = "Digite o nome da matéria."
            page.update()
            return

        try:
            add_subject(name)
        except (sqlite3.Error, OSError):
            message.value = "Não foi possível salvar. Verifique o acesso ao banco."
        else:
            name_field.value = ""
            try:
                refresh_subjects()
                message.value = "Matéria salva neste dispositivo."
            except (sqlite3.Error, OSError):
                message.value = "Matéria salva, mas não foi possível atualizar a lista. Reabra o aplicativo."
        page.update()

    try:
        initialize_database()
        refresh_subjects()
    except (sqlite3.Error, OSError):
        page.add(ft.Text("Não foi possível abrir o banco local. Verifique o acesso à pasta de dados."))
        return

    # SafeArea evita sobreposição com as barras do sistema no celular.
    page.add(
        ft.SafeArea(
            content=ft.Column(
                controls=[
                    ft.Text("Streakly", size=28),
                    ft.Text("Minhas matérias"),
                    name_field,
                    ft.Button("Salvar matéria", on_click=save_subject),
                    message,
                    subjects,
                ],
                expand=True,
            ),
            expand=True,
        )
    )


if __name__ == "__main__":
    ft.run(main)
