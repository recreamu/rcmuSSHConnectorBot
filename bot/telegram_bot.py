import asyncio
import asyncssh
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, FSInputFile
)
from aiogram.filters import Command
from tempfile import NamedTemporaryFile

from botToken import TOKEN

# ======== Инициализация ========
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ======== Временное хранилище для SSH данных ========
# ключи: ip, port, username, password, input_mode (bool), editing (bool)
user_data: dict[int, dict] = {}
active_sessions = {}  # user_id: SSHClientConnection
pending_commands: dict[int, str] = {}
pending_uploads: dict[int, dict] = {}  # uid: {"local_path": str, "remote_path": str, "file_name": str}
BLACKLIST = {'nano', 'vim', 'vi', 'top', 'htop', 'less', 'more'}

# ======== Клавиатуры ========
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Пользователь"), KeyboardButton(text="Инструменты")]
    ],
    resize_keyboard=True
)

edit_button_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Изменить", callback_data="edit_data")]
    ]
)


# клавиатура для принудительного выполнения
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
force_exec_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Выполнить в любом случае", callback_data="force_exec")]
    ]
)


def get_tools_kb(user_id: int) -> ReplyKeyboardMarkup:
    data = user_data.get(user_id, {})
    mode = data.get("input_mode", False)
    input_button_text = "Сессия: Вкл✅" if mode else "Сессия: Выкл⛔"
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=input_button_text),
                KeyboardButton(text="Скачать из текущ. директории"),
                KeyboardButton(text="Загрузить в текущ. директорию"),
                KeyboardButton(text="Скачать текущ. директорию"),
            ],
            [KeyboardButton(text="Назад")]
        ],
        resize_keyboard=True
    )

# ======== Обработчики ========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = message.from_user.id
    if uid not in user_data:
        user_data[uid] = {
            "ip": "192.168.0.1",
            "port": "22",
            "username": "user",
            "password": "pass",
            "input_mode": False,
            "editing": False,
            "current_path": ".",
            "download_mode": False,
            "upload_mode": False,
        }
    await message.answer("Добро пожаловать! Используйте кнопки ниже:", reply_markup=main_kb)

@dp.message(F.text == "Пользователь")
async def user_info(message: Message):
    uid = message.from_user.id
    data = user_data.get(uid)
    if not data:
        return await message.answer("Нет данных. Введите /start.")
    info = (
        f"🔒 SSH данные:\n"
        f"IP: {data['ip']}\n"
        f"Порт: {data['port']}\n"
        f"Пользователь: {data['username']}\n"
        f"Пароль: {data['password']}\n\n"
        f"Данные не сохраняются в постоянной памяти, они будут очищены после перезагрузки бота."
    )
    await message.answer(info, reply_markup=edit_button_kb)

@dp.callback_query(F.data == "edit_data")
async def start_edit_data(call: CallbackQuery):
    uid = call.from_user.id
    if uid not in user_data:
        user_data[uid] = {"editing": False, "input_mode": False}
    user_data[uid]["editing"] = True
    await call.message.answer(
        "Введите новые SSH‑данные через запятую:\n"
        "ip,port,username,password"
    )
    await call.answer()

@dp.message(F.text == "Инструменты")
async def tools_handler(message: Message):
    await message.answer("Выберите инструмент:", reply_markup=get_tools_kb(message.from_user.id))

@dp.message(F.text == "Назад")
async def back_handler(message: Message):
    await message.answer("Главное меню:", reply_markup=main_kb)

@dp.message(F.text == "Скачать из текущ. директории")
async def start_download_mode(message: Message):
    uid = message.from_user.id
    data = user_data[uid]

    # Проверка: режим ввода выключен или нет активной сессии
    if not data.get("input_mode") or uid not in active_sessions:
        return await message.answer("⚠️ Сначала включите сессию, чтобы использовать эту функцию.")

    try:
        if uid in active_sessions:
            conn, process = active_sessions[uid]
            process.stdin.write("pwd\n")
            await asyncio.sleep(0.1)
            output = await process.stdout.read(1024)
            output = re.sub(r'\x1B\].*?(?:\x07|\x1B\\)', '', output)
            output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', output)
            lines = output.strip().splitlines()

            for line in lines:
                if line.startswith("/"):
                    data["current_path"] = line.strip()
                    break
    except Exception as e:
        await message.answer(f"⚠️ Не удалось определить путь: {e}")

    data["download_mode"] = True
    await message.answer(
        f"Скачивание из: {data['current_path']}\n"
        "Введите имя файла для загрузки:"
    )

@dp.message(F.text == "Загрузить в текущ. директорию")
async def start_upload_mode(message: Message):
    uid = message.from_user.id
    data = user_data[uid]

    # Проверка: режим ввода выключен или нет активной сессии
    if not data.get("input_mode") or uid not in active_sessions:
        return await message.answer("⚠️ Сначала включите сессию, чтобы использовать эту функцию.")

    try:
        if uid in active_sessions:
            conn, process = active_sessions[uid]

            # отправляем команду в уже активную сессию PTY
            process.stdin.write("pwd\n")
            await asyncio.sleep(0.1)
            output = await process.stdout.read(1024)

            # чистим от лишнего
            output = re.sub(r'\x1B\].*?(?:\x07|\x1B\\)', '', output)
            output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', output)
            lines = output.strip().splitlines()

            for line in lines:
                if line.startswith("/"):
                    data["current_path"] = line.strip()
                    break

        display_path = data.get("current_path", ".")
    except Exception as e:
        await message.answer(f"⚠️ Не удалось определить путь: {e}")
        display_path = data.get("current_path", ".")

    data["upload_mode"] = True
    await message.answer(
        f"Загрузка в: {display_path}\n"
        "Оправьте файл в следующем сообщении:"
    )

@dp.message(F.text == "Скачать текущ. директорию")
async def ask_download_directory(message: Message):
    uid = message.from_user.id
    data = user_data.get(uid)

    # Проверка: включён ли input_mode и активна ли сессия
    if not data.get("input_mode") or uid not in active_sessions:
        return await message.answer("⚠️ Сначала включите сессию, чтобы использовать эту функцию.")

    try:
        conn, process = active_sessions[uid]
        process.stdin.write("pwd\n")
        await asyncio.sleep(0.1)
        raw_output = await process.stdout.read(4096)
        clean = re.sub(r'\x1B\].*?(?:\x07|\x1B\\)', '', raw_output)
        clean = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', clean)
        lines = clean.strip().splitlines()

        # Найдём первую строку, которая начинается с /
        for line in lines:
            if line.startswith("/"):
                path = line.strip()
                break
        else:
            path = data.get("current_path", ".")

        data["current_path"] = path

        # Сохраняем флаг подтверждения
        data["confirm_dir_download"] = True

        # Клавиатура подтверждения
        confirm_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_download_dir"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_download_dir"),
                ]
            ]
        )

        await message.answer(
            f"📦 Скачивание директории: <code>{path}</code>\nВы уверены?",
            parse_mode="HTML",
            reply_markup=confirm_kb
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка определения пути:\n{e}")

@dp.callback_query(F.data == "confirm_download_dir")
async def confirm_download_dir(callback: CallbackQuery):
    uid = callback.from_user.id
    data = user_data.get(uid)

    if not data.get("confirm_dir_download"):
        return await callback.answer("⚠️ Запрос на скачивание директории не активен.")

    data["confirm_dir_download"] = False  # сбрасываем

    try:
        conn = active_sessions[uid][0]
        current_path = data.get("current_path", ".")

        archive_name = f"/tmp/{uid}_dir.tar.gz"

        # Архивируем через SSH
        result = await conn.run(f"tar -czf {archive_name} -C '{current_path}' .", check=False)
        if result.stderr:
            return await callback.message.answer(f"❌ Ошибка архивации:\n{result.stderr}")

        # Скачиваем через SFTP
        async with conn.start_sftp_client() as sftp:
            local_path = f"/tmp/{uid}_download.tar.gz"
            await sftp.get(archive_name, local_path)

        await callback.message.answer_document(FSInputFile(local_path, filename="directory.tar.gz"))
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при скачивании директории:\n{e}")
    finally:
        await callback.answer()


@dp.callback_query(F.data == "cancel_download_dir")
async def cancel_download_dir(callback: CallbackQuery):
    uid = callback.from_user.id
    user_data[uid]["confirm_dir_download"] = False
    await callback.message.answer("❌ Скачивание директории отменено.")
    await callback.answer()


@dp.callback_query(F.data == "confirm_upload")
async def confirm_upload_handler(call: CallbackQuery):
    uid = call.from_user.id
    data = pending_uploads.pop(uid, None)
    await call.answer()

    if not data:
        return await call.message.answer("⛔ Нечего загружать.")

    try:
        conn = active_sessions[uid][0]
        async with conn.start_sftp_client() as sftp:
            await sftp.put(data["local_path"], data["remote_path"])
        await call.message.answer("✅ Файл успешно заменён.")
    except Exception as e:
        await call.message.answer(f"❌ Ошибка при загрузке: {e}")

@dp.callback_query(F.data == "cancel_upload")
async def cancel_upload_handler(call: CallbackQuery):
    uid = call.from_user.id
    data = pending_uploads.pop(uid, None)
    await call.answer()
    await call.message.answer("🚫 Загрузка отменена.")


@dp.callback_query(F.data == "force_exec")
async def force_execute(callback: CallbackQuery):
    uid = callback.from_user.id
    cmd = pending_commands.pop(uid, None)
    await callback.answer()  # убираем «часики»
    if not cmd:
        return await callback.message.answer("Нет команды для выполнения.")

    session = active_sessions.get(uid)
    if not session:
        return await callback.message.answer("Сессия закрыта, включите ввод заново.")

    conn, process = session
    # шлём в PTY точно так же, как в основном хендлере:
    process.stdin.write(cmd + "\n")
    await asyncio.sleep(0.1)
    output = await process.stdout.read(65536)
    # очистка ANSI‑кодов как у вас
    import re
    output = re.sub(r'\x1B\].*?(?:\x07|\x1B\\)', '', output)
    output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', output).strip()

    if output:
        await callback.message.answer(f"<pre>{output}</pre>", parse_mode="HTML")
    else:
        await callback.message.answer("📥 Команда выполнена, вывода нет.")



# === НОВЫЙ обработчик: сначала ловим, если пользователь в режиме редактирования ===
@dp.message()
async def process_new_data_or_continue(message: Message):
    uid = message.from_user.id
    data = user_data.get(uid)
    if not data:
        return await message.answer("Введите /start, чтобы инициализировать данные.")

    # === Режим редактирования SSH-данных ===
    if data.get("editing", False):
        parts = [p.strip() for p in message.text.split(",")]
        if len(parts) != 4:
            return await message.answer(
                "Неверный формат. Пример:\n192.168.0.1,22,root,qwerty"
            )
        ip, port, user, pwd = parts
        old = user_data[uid]
        mode = old.get("input_mode", False)
        cp = old.get("current_path", ".")
        dl = old.get("download_mode", False)
        ul = old.get("upload_mode", False)
        user_data[uid] = {
            "ip": ip,
            "port": port,
            "username": user,
            "password": pwd,
            "input_mode": mode,
            "editing": False,
            "current_path": cp,
            "download_mode": dl,
            "upload_mode": ul,
        }
        return await message.answer("✅ Данные обновлены!", reply_markup=main_kb)

    text = message.text

    # === Переключатель режима ввода ===
    if text in ("Сессия: Выкл⛔", "Сессия: Вкл✅"):
        # Если пытаемся включить ввод — проверяем SSH-подключение
        if text == "Сессия: Выкл⛔":
            try:
                conn = await asyncssh.connect(
                    data["ip"],
                    port=int(data["port"]),
                    username=data["username"],
                    password=data["password"],
                    known_hosts=None
                )
                # создаём интерактивный shell (PTY)
                process = await conn.create_process(term_type="xterm")
                active_sessions[uid] = (conn, process)
                data["input_mode"] = True
                new_text = "Сессия: Вкл✅"
            except Exception as e:
                return await message.answer(f"❌ Ошибка SSH-подключения:\n{e}")
        else:
            # выключаем ввод и закрываем соединение
            conn = active_sessions.pop(uid, None)
            if conn:
                conn, process = active_sessions.pop(uid, (None, None))
                if process:
                    process.stdin.write("exit\n")
                    await process.wait_closed()
                if conn:
                    conn.close()
                data["input_mode"] = False
            new_text = "Сессия: Выкл⛔"

        return await message.answer(
            f"Режим ввода переключён на: {new_text}",
            reply_markup=get_tools_kb(uid)
        )

    # === загрузка и скачивание ===
    # Если мы в режиме скачивания, и получил не документ, а имя файла:
    if data.get("download_mode"):
        filename = message.text.strip()
        data["download_mode"] = False

        try:
            conn, _ = active_sessions[uid]
            async with conn.start_sftp_client() as sftp:
                remote_path = f"{data['current_path'].rstrip('/')}/{filename}"
                local = f"/tmp/{uid}_{filename}"
                await sftp.get(remote_path, local)

            await message.answer_document(FSInputFile(path=local, filename=filename))
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")
        return

    # обработчик загрузки в
    # === Обработка загрузки файла ===
    if data.get("upload_mode") and message.document:
        data["upload_mode"] = False  # сбрасываем режим в начале
        uid = message.from_user.id
        file_name = message.document.file_name

        # Сохраняем документ во временный файл
        try:
            downloaded = await bot.download(message.document)
            local_path = f"/tmp/{uid}_{file_name}"
            with open(local_path, "wb") as f:
                f.write(downloaded.read())
        except Exception as e:
            return await message.answer(f"❌ Ошибка при загрузке файла: {e}")

        # Проверяем существование на сервере
        try:
            conn, _ = active_sessions[uid]
            async with conn.start_sftp_client() as sftp:
                remote_path = f"{data['current_path'].rstrip('/')}/{file_name}"

                try:
                    await sftp.stat(remote_path)  # Проверка: существует ли файл?
                    # Если существует — спрашиваем подтверждение
                    pending_uploads[uid] = {
                        "local_path": local_path,
                        "remote_path": remote_path,
                        "file_name": file_name
                    }
                    await message.answer(
                        f"⚠️ Файл `{file_name}` уже существует. Заменить?",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [
                                InlineKeyboardButton(text="Заменить", callback_data="confirm_upload"),
                                InlineKeyboardButton(text="Отменить", callback_data="cancel_upload")
                            ]
                        ])
                    )
                except asyncssh.SFTPNoSuchFile:
                    # Файл не существует — сразу загружаем
                    await sftp.put(local_path, remote_path)
                    await message.answer("✅ Файл загружен.")

        except Exception as e:
            await message.answer(f"❌ Ошибка при загрузке: {e}")

        return

    # Если в режиме загрузки, но не файл — отмена
    elif data.get("upload_mode"):
        data["upload_mode"] = False
        return await message.answer("Загрузка отменена.")

    # === Обработка SSH‑команд в активном режиме PTY ===
    if data.get("input_mode"):
        session = active_sessions.get(uid)
        if not session:
            data["input_mode"] = False
            return await message.answer("❌ Сессия была прервана. Режим ввода выключен.")

        conn, process = session
        cmd = message.text.strip()

        # ——— Проверка на опасные команды ———
        cmd_name = cmd.split()[0]
        if cmd_name in BLACKLIST and uid not in pending_commands:
            pending_commands[uid] = cmd
            return await message.answer(
                "⚠️ Лучше не использовать эту команду здесь, "
                "интерфейс редактора не адаптирован под чат. "
                "Скачайте файл и отредактируйте на своём устройстве.",
                reply_markup=force_exec_kb
            )

        # 1) Если это cd — отправляем в PTY и затем делаем pwd для уточнения
        is_cd = cmd.startswith("cd ")

        process.stdin.write(cmd + "\n")
        await asyncio.sleep(0.1)

        # 2) Читаем вывод после основной команды
        output = await process.stdout.read(65536)
        output = re.sub(r'\x1B\].*?(?:\x07|\x1B\\)', '', output)
        output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', output).strip()

        # 3) Если это была команда cd, узнаем pwd и обновим current_path
        if is_cd:
            process.stdin.write("pwd\n")
            await asyncio.sleep(0.1)
            pwd_output = await process.stdout.read(65536)
            pwd_output = re.sub(r'\x1B\].*?(?:\x07|\x1B\\)', '', pwd_output)
            pwd_output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', pwd_output).strip()

            # Уточняем текущую директорию
            if pwd_output:
                data["current_path"] = pwd_output

        # 4) Отправляем ответ пользователю
        if output:
            return await message.answer(f"<pre>{output}</pre>", parse_mode="HTML")
        else:
            return await message.answer("📥 Команда выполнена. Вывода нет.")


# ======== Запуск ========
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
