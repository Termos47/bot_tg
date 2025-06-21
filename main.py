import os
import re
import time
import threading
import feedparser
from dotenv import load_dotenv
import telebot
import logging
from datetime import datetime
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, BotCommand
import requests
import json
from typing import Any, Union, List, Optional, Type, Callable
from PIL import Image, ImageDraw, ImageFont
import textwrap
import random
import traceback

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("rss_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('RSSBot')

# Загрузка переменных окружения
load_dotenv()

# Безопасное чтение переменных окружения с проверкой типов
def get_env_var(
    name: str, 
    default: Any = None, 
    required: bool = False, 
    var_type: Union[Type[str], Type[int], Type[bool], Type[list]] = str
) -> Any:
    value = os.getenv(name, default)
    if required and value is None:
        logger.critical(f"Environment variable {name} is required but not set")
        exit(1)
    
    try:
        if var_type is int:
            return int(value) if value is not None else default
        elif var_type is list:
            if value and isinstance(value, str):
                # Удаляем квадратные скобки, если они есть
                value = value.strip("[]")
                # Разделяем по запятым и очищаем элементы
                return [url.strip().strip("'\"") for url in value.split(',') if url.strip()]
            return default if default is not None else []
        elif var_type is bool:
            if isinstance(value, str):
                return value.lower() in ['true', '1', 'yes', 'y']
            return bool(value)
        return value
    except (TypeError, ValueError) as e:
        logger.error(f"Error converting {name} to {var_type.__name__}: {str(e)}")
        return default

# Чтение конфигурации
TOKEN: str = get_env_var('TELEGRAM_TOKEN', required=True)
CHANNEL_ID: str = get_env_var('CHANNEL_ID', required=True)
OWNER_ID: int = get_env_var('OWNER_ID', required=True, var_type=int)
RSS_URLS: List[str] = get_env_var(
    'RSS_URLS', 
    default="https://www.interfax.ru/rss.asp", 
    var_type=list
)
CHECK_INTERVAL: int = get_env_var('CHECK_INTERVAL', default=300, var_type=int)

# YandexGPT settings
YANDEX_API_KEY: Optional[str] = get_env_var('YANDEX_API_KEY')
YANDEX_FOLDER_ID: str = get_env_var('YANDEX_FOLDER_ID', default='')
DISABLE_YAGPT: bool = get_env_var('DISABLE_YAGPT', default=False, var_type=bool)

# Настройки для генерации изображений
FONTS_DIR: str = get_env_var('FONTS_DIR', default='fonts')
TEMPLATES_DIR: str = get_env_var('TEMPLATES_DIR', default='templates')
OUTPUT_DIR: str = get_env_var('OUTPUT_DIR', default='temp_images')
DEFAULT_FONT: str = get_env_var('DEFAULT_FONT', default='Montserrat-Bold.ttf')

bot = telebot.TeleBot(TOKEN)
sent_entries = set()

# Статистика работы бота
stats = {
    'start_time': None,
    'posts_sent': 0,
    'last_check': None,
    'errors': 0,
    'last_post': None,
    'yagpt_used': 0,
    'yagpt_errors': 0,
    'images_generated': 0
}

# Класс для генерации изображений с заголовками
class ImageGenerator:
    def __init__(self, templates_dir: str, fonts_dir: str, output_dir: str):
        self.templates_dir = templates_dir
        self.fonts_dir = fonts_dir
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.templates_dir, exist_ok=True)
        os.makedirs(self.fonts_dir, exist_ok=True)
        
    def generate_image(self, title: str) -> Optional[str]:
        """Генерирует изображение с заголовком новости"""
        try:
            # Получаем список доступных шаблонов
            templates = [f for f in os.listdir(self.templates_dir) 
                        if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
            
            if not templates:
                logger.warning("No templates found. Using default background")
                # Создаем простой фон, если шаблонов нет
                img = Image.new('RGB', (1200, 630), color=(40, 40, 40))
            else:
                # Выбираем случайный шаблон
                template_file = random.choice(templates)
                template_path = os.path.join(self.templates_dir, template_file)
                img = Image.open(template_path).convert('RGB')
            
            draw = ImageDraw.Draw(img)
            
            # Загружаем шрифт
            font_path = os.path.join(self.fonts_dir, DEFAULT_FONT)
            try:
                # Определяем размер шрифта в зависимости от размера изображения
                base_font_size = max(10, min(img.width, img.height) // 15)
                font = ImageFont.truetype(font_path, base_font_size)
            except IOError:
                logger.warning(f"Font {DEFAULT_FONT} not found. Using default font")
                font = ImageFont.load_default()
                base_font_size = 20
            
            # Настройки текста
            text_color = (255, 255, 255)  # Белый цвет
            stroke_color = (0, 0, 0)      # Черный контур
            stroke_width = 2
            max_width = img.width - 100   # Отступы по бокам
            max_lines = 3                 # Максимум строк
            
            # Разбиваем заголовок на строки
            lines = []
            words = title.split()
            current_line = ""
            
            for word in words:
                test_line = current_line + word + " "
                # Оцениваем ширину текста через bbox
                text_bbox = draw.textbbox((0, 0), test_line, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                if text_width <= max_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line.strip())
                    current_line = word + " "
            
            if current_line:
                lines.append(current_line.strip())
            
            # Ограничиваем количество строк
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                if len(lines[-1]) > 15:
                    lines[-1] = lines[-1][:-3] + "..."
            
            # Рассчитываем высоту строки
            test_bbox = draw.textbbox((0, 0), "Test", font=font)
            line_height = int((test_bbox[3] - test_bbox[1]) * 1.2)
            total_height = len(lines) * line_height
            
            # Позиционирование текста (по центру)
            y_position = (img.height - total_height) // 2
            
            # Рисуем каждую строку текста
            for line in lines:
                # Рассчитываем ширину текста для центрирования
                text_bbox = draw.textbbox((0, 0), line, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                x_position = (img.width - text_width) // 2
                
                # Рисуем текст с контуром для лучшей читаемости
                draw.text(
                    (x_position, y_position),
                    line,
                    font=font,
                    fill=text_color,
                    stroke_fill=stroke_color,
                    stroke_width=stroke_width
                )
                y_position += line_height
            
            # Сохраняем изображение
            os.makedirs(self.output_dir, exist_ok=True)
            output_path = os.path.join(self.output_dir, f"post_{int(time.time())}.jpg")
            img.save(output_path)
            
            stats['images_generated'] += 1
            return output_path
        
        except Exception as e:
            logger.error(f"Image generation failed: {str(e)}")
            logger.error(traceback.format_exc())
            return None

# Инициализация генератора изображений
image_generator = ImageGenerator(
    templates_dir=TEMPLATES_DIR,
    fonts_dir=FONTS_DIR,
    output_dir=OUTPUT_DIR
)

def enhance_with_yagpt(title: str, description: str) -> Optional[dict]:
    """Улучшает текст поста с помощью YandexGPT через REST API"""
    if DISABLE_YAGPT or not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return None

    # Ограничение длины входных данных
    MAX_INPUT_LENGTH = 3000
    if len(description) > MAX_INPUT_LENGTH:
        description = description[:MAX_INPUT_LENGTH] + "..."

    prompt = f"""
Ты — профессиональный редактор новостей. Перепиши заголовок и описание новостного поста для Telegram-канала, чтобы они были:
1. Более привлекательными и цепляющими
2. Легко читаемыми
3. Сохраняли суть оригинала
4. Оптимизированными под соцсети (используй эмодзи, абзацы)
5. Добавь релевантные эмодзи в заголовок
6. Сделай текст более живым и интересным
7. Убери лишние детали, оставив суть
8. Максимальная длина заголовка: 100 символов
9. Максимальная длина описания: 400 символов

Ответ в формате JSON: {{"title": "новый заголовок", "description": "новое описание"}}

Оригинальный заголовок: {title}
Оригинальное описание: {description}
    """

    try:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {YANDEX_API_KEY}"
        }
        
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {
                "temperature": 0.4,
                "maxTokens": 1500
            },
            "messages": [
                {
                    "role": "user",
                    "text": prompt
                }
            ]
        }

        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()

        # Извлечение текста ответа
        result_text = result['result']['alternatives'][0]['message']['text']

        try:
            # Извлекаем JSON из текста ответа
            json_start = result_text.find('{')
            json_end = result_text.rfind('}') + 1
            if json_start == -1 or json_end == 0:
                logger.error(f"YandexGPT response format error: {result_text}")
                return None
                
            json_str = result_text[json_start:json_end]
            data = json.loads(json_str)
            return {
                'title': data.get('title', title),
                'description': data.get('description', description)
            }
        except json.JSONDecodeError as e:
            logger.error(f"YandexGPT JSON error: {e}\nResponse: {result_text}")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"YandexGPT request error: {str(e)}")
        stats['yagpt_errors'] += 1
    except Exception as e:
        logger.error(f"YandexGPT processing error: {str(e)}")
        stats['yagpt_errors'] += 1
    
    return None

# Надёжный механизм управления потоком
class BotController:
    def __init__(self):
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.last_check = datetime.now()
        
    def start(self) -> bool:
        if self.is_running:
            return False
            
        self.is_running = True
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self.rss_loop, daemon=True)
        self.worker_thread.start()
        
        # Запись статистики
        stats['start_time'] = datetime.now()
        stats['last_check'] = None
        stats['posts_sent'] = 0
        stats['errors'] = 0
        stats['yagpt_used'] = 0
        stats['yagpt_errors'] = 0
        stats['images_generated'] = 0
        
        return True
        
    def stop(self) -> bool:
        if not self.is_running:
            return False
            
        self.is_running = False
        self.stop_event.set()
        
        # Ожидаем завершение потока (максимум 5 секунд)
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5.0)
            
        return True
        
    def status(self) -> bool:
        return self.is_running
        
    def rss_loop(self) -> None:
        logger.info("===== RSS LOOP STARTED =====")
        while self.is_running and not self.stop_event.is_set():
            try:
                self.last_check = datetime.now()
                stats['last_check'] = self.last_check
                logger.info(f"Checking {len(RSS_URLS)} RSS feeds")
                
                for url in RSS_URLS:
                    if self.stop_event.is_set():
                        break
                        
                    try:
                        feed = feedparser.parse(url)
                        if not feed.entries:
                            logger.warning(f"Empty feed: {url}")
                            continue
                            
                        # Обработка новых записей
                        for entry in reversed(feed.entries[:10]):
                            if self.stop_event.is_set():
                                break
                                
                            if not hasattr(entry, 'link') or entry.link in sent_entries:
                                continue
                                
                            try:
                                message, image_path = self.format_message(entry)
                                
                                # Отправка с изображением, если доступно
                                if image_path and os.path.exists(image_path):
                                    try:
                                        with open(image_path, 'rb') as photo:
                                            bot.send_photo(
                                                chat_id=CHANNEL_ID,
                                                photo=photo,
                                                caption=message,
                                                parse_mode='HTML'
                                            )
                                        # Удаляем временный файл после отправки
                                        os.remove(image_path)
                                        logger.info(f"Image sent and removed: {image_path}")
                                    except Exception as e:
                                        logger.error(f"Error sending photo: {str(e)}")
                                        # Пробуем отправить без изображения
                                        bot.send_message(
                                            chat_id=CHANNEL_ID,
                                            text=message,
                                            parse_mode='HTML'
                                        )
                                else:
                                    bot.send_message(
                                        chat_id=CHANNEL_ID,
                                        text=message,
                                        parse_mode='HTML'
                                    )
                                
                                sent_entries.add(entry.link)
                                stats['posts_sent'] += 1
                                stats['last_post'] = datetime.now()
                                logger.info(f"Posted: {entry.link}")
                                
                                # Пауза между постами
                                time.sleep(3)
                                
                            except Exception as e:
                                logger.error(f"Send error: {str(e)}")
                                stats['errors'] += 1
                                
                    except Exception as e:
                        logger.error(f"Feed error ({url}): {str(e)}")
                        stats['errors'] += 1
                
                # Ожидание следующей проверки с возможностью прерывания
                logger.info(f"Cycle complete. Next check in {CHECK_INTERVAL} sec")
                self.stop_event.wait(CHECK_INTERVAL)
                
            except Exception as e:
                logger.critical(f"Loop error: {str(e)}")
                stats['errors'] += 1
                time.sleep(30)
                
        logger.info("===== RSS LOOP STOPPED =====")
    
    @staticmethod
    def format_message(entry: Any) -> tuple:
        title = entry.title if hasattr(entry, 'title') else "No title"
        description = entry.description if hasattr(entry, 'description') else ""
        link = entry.link if hasattr(entry, 'link') else ""
        
        # Очистка HTML
        clean: Callable[[str], str] = lambda text: re.sub(r'<[^>]+>', '', text) if text else ""
        title = clean(title)
        description = clean(description)
        
        original_title = title
        original_description = description
        
        # Улучшение текста с помощью YandexGPT
        if not DISABLE_YAGPT and YANDEX_API_KEY and YANDEX_FOLDER_ID:
            try:
                enhanced = enhance_with_yagpt(title, description)
                if enhanced:
                    new_title = enhanced.get('title')
                    new_description = enhanced.get('description')
                    
                    # Проверяем качество улучшения
                    if (new_title and len(new_title) > 10 and 
                        new_description and len(new_description) > 30 and
                        len(new_title) < 120 and len(new_description) < 600):
                        title = new_title
                        description = new_description
                        stats['yagpt_used'] += 1
                        logger.info("YandexGPT enhancement applied")
                    else:
                        logger.warning("YandexGPT output validation failed")
            except Exception as e:
                logger.error(f"YandexGPT integration error: {str(e)}")
                stats['yagpt_errors'] += 1

        # Сокращение описания
        if len(description) > 500:
            description = description[:500] + "..."
            
        # Генерация изображения с заголовком
        image_path = None
        try:
            # Используем оригинальный или улучшенный заголовок для изображения
            image_title = title if title else original_title
            if image_title:
                image_path = image_generator.generate_image(image_title)
                if image_path:
                    logger.info(f"Image generated: {image_path}")
                else:
                    logger.warning("Image generation returned no path")
        except Exception as e:
            logger.error(f"Image generation error: {str(e)}")
        
        # Форматирование сообщения
        message = f"<b>{title}</b>\n\n{description}\n\n<a href='{link}'>🔗 Читать полностью</a>"
        return message, image_path

# Инициализация контроллера
controller = BotController()

# Создаем клавиатуру с кнопками
def create_reply_keyboard() -> ReplyKeyboardMarkup:
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    
    # Первый ряд - управление
    if controller.status():
        markup.add(
            KeyboardButton("⏸️ Приостановить"),
            KeyboardButton("🛑 Остановить"),
        )
    else:
        markup.add(
            KeyboardButton("▶️ Запустить"),
            KeyboardButton("🔄 Перезапустить"),
        )
    
    # Второй ряд - информация
    markup.add(
        KeyboardButton("📊 Статистика"),
        KeyboardButton("📝 Источники"),
    )
    
    # Третий ряд - помощь и информация
    markup.add(
        KeyboardButton("❓ Помощь"),
        KeyboardButton("ℹ️ Инфо")
    )
    
    return markup

# Регистрируем команды для бокового меню
bot.set_my_commands([
    BotCommand("start", "Запустить бота"),
    BotCommand("help", "Помощь и команды"),
    BotCommand("status", "Текущий статус"),
    BotCommand("stats", "Статистика работы"),
    BotCommand("start_bot", "Запустить публикацию"),
    BotCommand("pause", "Приостановить"),
    BotCommand("stop", "Остановить бота"),
    BotCommand("restart", "Перезапустить"),
    BotCommand("sources", "Список источников"),
    BotCommand("yagpt_status", "Статус YandexGPT")
])

# Информация о боте
INFO_MESSAGE = """
ℹ️ <b>Информация о боте</b>

🤖 <b>Автопостинг новостей в Telegram-канал</b>

Этот бот автоматически публикует новости из RSS-лент в ваш канал. 
Просто настройте источники, и бот будет регулярно проверять их 
на наличие новых материалов.

<b>Основные функции:</b>
• Автоматическая публикация новостей
• Генерация изображений с заголовками
• Улучшение текстов с помощью ИИ (YandexGPT)
• Гибкая настройка источников
• Подробная статистика работы

<b>Технические характеристики:</b>
• Поддержка множества RSS-источников
• Автоматическая обработка изображений
• Интеллектуальное форматирование текста
• Управление через Telegram-интерфейс

<b>Версия:</b> 5.0 (Image Generation Pro) (Июль 2025)
"""

# Краткое описание бота
BOT_DESCRIPTION = """
🤖 <b>Автопостинг новостей в Telegram-канал</b>

Этот бот автоматически публикует новости из RSS-лент в ваш канал. 
Просто настройте источники, и бот будет регулярно проверять их 
на наличие новых материалов.

<b>Основные функции:</b>
• Автоматическая публикация новостей
• Генерация изображений с заголовками
• Фильтрация свежих новостей
• Удобное управление
• Улучшение текстов с помощью YandexGPT
"""

# Список всех команд
COMMANDS_LIST = """
<b>Доступные команды:</b>

/start - Запустить бота и показать это сообщение
/help - Показать список команд
/status - Текущий статус бота
/stats - Статистика работы
/start_bot - Запустить публикацию новостей
/pause - Приостановить публикацию
/stop - Полностью остановить бота
/restart - Перезапустить бота
/sources - Список источников новостей
/yagpt_status - Статус интеграции с YandexGPT

<b>Используйте кнопки внису для быстрого доступа к командам 👇</b>
"""

# Функции для генерации отчетов
def generate_status_report() -> str:
    """Генерация отчёта о состоянии бота"""
    if not stats['start_time']:
        return "❓ Бот в настоящее время остановлен"
    
    uptime = datetime.now() - stats['start_time']
    hours, remainder = divmod(uptime.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    last_check = stats['last_check'].strftime("%H:%M:%S") if stats['last_check'] else "никогда"
    last_post = stats['last_post'].strftime("%H:%M:%S") if stats['last_post'] else "никогда"
    
    report = (
        f"🤖 <b>Статус бота</b>\n"
        f"⏱ Время работы: {int(hours)}ч {int(minutes)}м\n"
        f"📊 Отправлено новостей: {stats['posts_sent']}\n"
        f"🖼 Сгенерировано изображений: {stats['images_generated']}\n"
        f"❌ Ошибки: {stats['errors']}\n"
        f"🔄 Последняя проверка: {last_check}\n"
        f"📬 Последняя публикация: {last_post}\n"
        f"🔗 Источников: {len(RSS_URLS)}\n"
        f"📝 Состояние: {'работает ▶️' if controller.status() else 'остановлен 🛑'}"
    )
    return report

def generate_stats_report() -> str:
    """Генерация статистического отчёта"""
    if not stats['start_time']:
        return "📊 Статистика недоступна: бот не запущен"
    
    uptime = datetime.now() - stats['start_time']
    hours = uptime.total_seconds() / 3600
    posts_per_hour = stats['posts_sent'] / hours if hours > 0 else 0
    
    report = (
        f"📈 <b>Статистика бота</b>\n"
        f"⏱ Время работы: {str(uptime).split('.')[0]}\n"
        f"📊 Всего отправлено новостей: {stats['posts_sent']}\n"
        f"🖼 Сгенерировано изображений: {stats['images_generated']}\n"
        f"📮 Средняя скорость: {posts_per_hour:.1f} новостей/час\n"
        f"❌ Всего ошибок: {stats['errors']}\n"
        f"🔗 Источников: {len(RSS_URLS)}\n"
        f"🆔 Канал: {CHANNEL_ID}\n"
        f"🕒 Последняя активность: {stats['last_check'].strftime('%Y-%m-%d %H:%M') if stats['last_check'] else 'N/A'}"
    )
    
    # Добавляем информацию о YandexGPT
    report += (
        f"\n\n🧠 <b>YandexGPT</b>\n"
        f"Статус: {'включен ✅' if not DISABLE_YAGPT else 'выключен ⚠️'}\n"
        f"API ключ: {'установлен' if YANDEX_API_KEY else 'отсутствует'}\n"
        f"Каталог: {'указан' if YANDEX_FOLDER_ID else 'не указан'}\n"
        f"Использовано: {stats['yagpt_used']} раз\n"
        f"Ошибки: {stats['yagpt_errors']}"
    )
    return report

def generate_combined_report() -> str:
    """Объединенный отчет: статус + статистика"""
    status = generate_status_report()
    stats_report = generate_stats_report()
    return f"{status}\n\n{stats_report}"

def list_sources() -> str:
    """Форматированный список источников"""
    sources = "\n".join([f"• {i+1}. {url}" for i, url in enumerate(RSS_URLS)])
    return f"📚 <b>Источники новостей</b> ({len(RSS_URLS)}):\n{sources}"

def get_yagpt_status() -> str:
    """Статус интеграции с YandexGPT"""
    status = "🟢 Активна" if not DISABLE_YAGPT else "🔴 Отключена"
    key_status = "🟢 Установлен" if YANDEX_API_KEY else "🔴 Отсутствует"
    folder_status = "🟢 Указан" if YANDEX_FOLDER_ID else "⚠️ Не указан"
    
    report = (
        f"🧠 <b>Статус YandexGPT</b>\n\n"
        f"• Интеграция: {status}\n"
        f"• API ключ: {key_status}\n"
        f"• Каталог: {folder_status}\n"
        f"• Использовано: {stats['yagpt_used']} раз\n"
        f"• Ошибки: {stats['yagpt_errors']}"
    )
    
    if DISABLE_YAGPT or not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        report += "\n\nℹ️ Для активации установите переменные окружения:\n" \
                    "YANDEX_API_KEY и YANDEX_FOLDER_ID"
                
    return report

# Обработчики команд
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
        
    bot.reply_to(message, 
        f"{BOT_DESCRIPTION}\n\n{COMMANDS_LIST}",
        parse_mode="HTML",
        reply_markup=create_reply_keyboard()
    )

@bot.message_handler(commands=['status'])
def send_status(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
        
    bot.reply_to(message, generate_status_report(), 
                parse_mode="HTML",
                reply_markup=create_reply_keyboard())

@bot.message_handler(commands=['stats'])
def send_stats(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
        
    bot.reply_to(message, generate_combined_report(), 
                parse_mode="HTML",
                reply_markup=create_reply_keyboard())

@bot.message_handler(commands=['start_bot'])
def start_command(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
        
    if controller.start():
        bot.reply_to(message, "✅ Публикация начата! 🚀", 
                    parse_mode="HTML",
                    reply_markup=create_reply_keyboard())
    else:
        bot.reply_to(message, "⚠️ Бот уже запущен!", 
                    parse_mode="HTML",
                    reply_markup=create_reply_keyboard())

@bot.message_handler(commands=['pause', 'stop'])
def stop_command(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
        
    if controller.stop():
        bot.reply_to(message, "🛑 Публикация остановлена! ⏸️", 
                    parse_mode="HTML",
                    reply_markup=create_reply_keyboard())
    else:
        bot.reply_to(message, "⚠️ Бот уже остановлен!", 
                    parse_mode="HTML",
                    reply_markup=create_reply_keyboard())

@bot.message_handler(commands=['restart'])
def restart_command(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
        
    controller.stop()
    time.sleep(1)
    if controller.start():
        bot.reply_to(message, "🔄 Бот успешно перезапущен! 🔄", 
                    parse_mode="HTML",
                    reply_markup=create_reply_keyboard())
    else:
        bot.reply_to(message, "⚠️ Ошибка при перезапуске!", 
                    parse_mode="HTML",
                    reply_markup=create_reply_keyboard())

@bot.message_handler(commands=['sources'])
def sources_command(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
        
    bot.reply_to(message, list_sources(), 
                parse_mode="HTML",
                reply_markup=create_reply_keyboard())

@bot.message_handler(commands=['yagpt_status'])
def yagpt_status_command(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
        
    bot.reply_to(message, get_yagpt_status(), 
                parse_mode="HTML",
                reply_markup=create_reply_keyboard())

# Обработка текстовых сообщений (кнопок)
@bot.message_handler(func=lambda message: True)
def handle_text_messages(message: telebot.types.Message) -> None:
    if message.from_user.id != OWNER_ID: # type: ignore
        return
    
    text = message.text.strip() # type: ignore
    
    # Обработка кнопок
    if text == "▶️ Запустить":
        start_command(message)
    elif text == "⏸️ Приостановить" or text == "🛑 Остановить":
        stop_command(message)
    elif text == "🔄 Перезапустить":
        restart_command(message)
    elif text == "📊 Статистика":
        report = generate_combined_report()
        bot.reply_to(message, report, 
                    parse_mode="HTML",
                    reply_markup=create_reply_keyboard())
    elif text == "📝 Источники":
        sources_command(message)
    elif text == "❓ Помощь":
        send_welcome(message)
    elif text == "ℹ️ Инфо":
        bot.reply_to(message, INFO_MESSAGE, 
                    parse_mode="HTML",
                    reply_markup=create_reply_keyboard())
    else:
        bot.reply_to(message, "⚠️ Неизвестная команда. Используйте /help для списка команд",
                    reply_markup=create_reply_keyboard())

# Проверка доступа при запуске
def initial_check() -> Optional[str]:
    try:
        me = bot.get_me()
        logger.info(f"Bot started: @{me.username}")
        
        # Проверка канала
        bot.send_chat_action(CHANNEL_ID, 'typing')
        logger.info(f"Channel access OK: {CHANNEL_ID}")
        
        # Проверка RSS
        for url in RSS_URLS:
            feed = feedparser.parse(url)
            status = "OK" if feed.entries else "ERROR"
            logger.info(f"RSS check: {url} - {status}")
            
        # Проверка YandexGPT
        if not DISABLE_YAGPT and YANDEX_API_KEY and YANDEX_FOLDER_ID:
            logger.info("YandexGPT integration: ACTIVE")
        else:
            logger.info("YandexGPT integration: DISABLED")
            
        # Проверка генератора изображений
        logger.info("Image generator setup:")
        logger.info(f"  Fonts directory: {FONTS_DIR}")
        
        # Проверка наличия шрифта
        font_path = os.path.join(FONTS_DIR, DEFAULT_FONT)
        if os.path.exists(font_path):
            logger.info(f"  Main font: {DEFAULT_FONT} - FOUND")
        else:
            logger.warning(f"  Main font: {DEFAULT_FONT} - NOT FOUND! Using system default")
            
        # Проверка шаблонов
        logger.info(f"  Templates directory: {TEMPLATES_DIR}")
        templates = os.listdir(TEMPLATES_DIR) if os.path.exists(TEMPLATES_DIR) else []
        if templates:
            logger.info(f"  Found {len(templates)} templates")
        else:
            logger.warning("  No templates found! Using solid color backgrounds")
        
        # Тестовая генерация изображения
        test_image_path = image_generator.generate_image("Тест генерации изображения: Запуск бота")
        if test_image_path and os.path.exists(test_image_path):
            logger.info(f"Test image generated: {test_image_path}")
            # Отправляем тестовое изображение владельцу
            try:
                with open(test_image_path, 'rb') as photo:
                    bot.send_photo(OWNER_ID, photo, caption="✅ Тест генерации изображений пройден успешно!")
                os.remove(test_image_path)
            except Exception as e:
                logger.warning(f"Failed to send test image: {str(e)}")
        else:
            logger.warning("Test image generation failed")
            
        # Логирование конфигурации
        logger.info(f"Configuration:")
        logger.info(f"  TOKEN: {TOKEN[:5]}...{TOKEN[-5:]}")
        logger.info(f"  CHANNEL_ID: {CHANNEL_ID}")
        logger.info(f"  OWNER_ID: {OWNER_ID}")
        logger.info(f"  RSS_URLS: {RSS_URLS}")
        logger.info(f"  CHECK_INTERVAL: {CHECK_INTERVAL}")
        logger.info(f"  YANDEX_API_KEY: {'Set' if YANDEX_API_KEY else 'Not set'}")
        logger.info(f"  YANDEX_FOLDER_ID: {YANDEX_FOLDER_ID}")
        logger.info(f"  DISABLE_YAGPT: {DISABLE_YAGPT}")
            
    except Exception as e:
        logger.critical(f"STARTUP ERROR: {str(e)}")
        logger.error(traceback.format_exc())
        return f"⚠️ Ошибка при запуске: {str(e)}"
    return None

if __name__ == '__main__':
    logger.info("===== BOT STARTING (Image Generation Pro) =====")
    error = initial_check()
    
    if error:
        bot.send_message(OWNER_ID, error, parse_mode="HTML")
    
    logger.info("===== READY FOR COMMANDS =====")
    bot.infinity_polling()
