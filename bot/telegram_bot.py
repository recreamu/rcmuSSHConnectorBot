import asyncio
import asyncssh
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command

from botToken import TOKEN

# ======== Инициализация ========
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ======== Временное хранилище для SSH данных ========
# ключи: ip, port, username, password, input_mode (bool), editing (bool)
user_data: dict[int, dict] = {}

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

def get_tools_kb(user_id: int) -> ReplyKeyboardMarkup:
    data = user_data.get(user_id, {})
    mode = data.get("input_mode", False)
    input_button_text = "Ввод✅" if mode else "Ввод⛔"
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=input_button_text),
                KeyboardButton(text="Пусто1"),
                KeyboardButton(text="Пусто2"),
                KeyboardButton(text="Пусто3"),
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
            "editing": False
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
        mode = data.get("input_mode", False)
        user_data[uid] = {
            "ip": ip,
            "port": port,
            "username": user,
            "password": pwd,
            "input_mode": mode,
            "editing": False
        }
        return await message.answer("✅ Данные обновлены!", reply_markup=main_kb)

    text = message.text

    # === Переключатель режима ввода ===
    if text in ("Ввод⛔", "Ввод✅"):
        # Если пытаемся включить ввод — проверяем SSH-подключение
        if text == "Ввод⛔":
            try:
                conn = await asyncssh.connect(
                    data["ip"],
                    port=int(data["port"]),
                    username=data["username"],
                    password=data["password"],
                    known_hosts=None
                )
                conn.close()
            except Exception as e:
                return await message.answer(f"❌ Ошибка SSH-подключения:\n{e}")
            data["input_mode"] = True
            new_text = "Ввод✅"
        else:
            data["input_mode"] = False
            new_text = "Ввод⛔"

        return await message.answer(
            f"Режим ввода переключён на: {new_text}",
            reply_markup=get_tools_kb(uid)
        )

    # === Обработка SSH-команд в активном режиме ===
    if data.get("input_mode"):
        try:
            conn = await asyncssh.connect(
                data["ip"],
                port=int(data["port"]),
                username=data["username"],
                password=data["password"],
                known_hosts=None
            )
            result = await conn.run(message.text)
            conn.close()
            output = (result.stdout or "") + (result.stderr or "")
            output = output.strip()

            if output:
                return await message.answer(f"<pre>{output}</pre>", parse_mode="HTML")
            else:
                return await message.answer("📥 Команда выполнена. Вывода нет.")

        except Exception as e:
            return await message.answer(f"❌ Ошибка выполнения команды:\n{e}")

    # В остальных случаях — молчим
    return


# ======== Запуск ========
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
