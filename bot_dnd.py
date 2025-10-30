import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
import google.generativeai as genai
import random
import json
import re
import time
import os

# ====================== ФАЙЛЫ ======================
SESSION_FILE = 'dnd_sessions.json'
RULES_FILE = 'dm_rules.txt'  # ← Правила DM
HISTORY_DIR = '.'  # Можно сменить на папку, например: 'history/'

# ====================== НАСТРОЙКИ ======================
VK_TOKEN = 'vk1.a.ZNWIDHuspPzJmc6FLBxBhN4qifkK-TD1fkqKx4cZNTLSlySh-pFN9EHTZ5exofeNMtdKo1EHS0hmt3us_AzTTtuYA6CAKBC6i-SOOlbnK5ehxK31u3M4irUuAC1nVlaB7uFKbX8YQJm9P0cMsHPBaCRGnvKGzpajNaC_Ro4f7aR0ERODo2qmIIGuSxsHXAkC8KSztTDi4Wj0QIRf7aQ-gQ'
GROUP_ID = 233542113
GEMINI_KEY = 'AIzaSyBuhhv5kV2Zhbfen2xOHK9PNbhWmpISupg'
genai.configure(api_key=GEMINI_KEY)

# Хранилище сессий
dnd_sessions = {}

# Создаём папку для истории, если нужно
os.makedirs(HISTORY_DIR, exist_ok=True)

# ====================== ФУНКЦИИ ======================
def load_sessions():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                restored = {}
                for peer_id, sess in data.items():
                    peer_id = int(peer_id)
                    restored[peer_id] = {
                        'is_active': sess.get('is_active', False),
                        'history': sess.get('history', []),
                        'plot': sess.get('plot'),
                        'current_stage': sess.get('current_stage', 0)
                    }
                return restored
        except Exception as e:
            print(f"Ошибка загрузки сессий: {e}")
    return {}

def save_sessions():
    try:
        with open(SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(dnd_sessions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения сессий: {e}")

def get_history_file(peer_id):
    return os.path.join(HISTORY_DIR, f"session_history_{peer_id}.txt")

def save_history_to_file(peer_id, history):
    """Сохраняет историю в .txt файл для Gemini"""
    file_path = get_history_file(peer_id)
    with open(file_path, 'w', encoding='utf-8') as f:
        for msg in history:
            role = "DM" if msg['role'] == 'model' else "Игрок"
            text = msg['parts'][0] if isinstance(msg['parts'], list) else msg['parts']
            f.write(f"{role}: {text}\n\n")
    return file_path

def roll_dice(dice_type=20):
    return random.randint(1, dice_type)

def send_long_message(peer_id, text, max_length=4000):
    parts = []
    while len(text) > max_length:
        split_pos = text.rfind('\n', 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind(' ', 0, max_length)
        if split_pos == -1:
            split_pos = max_length
        parts.append(text[:split_pos])
        text = text[split_pos:].lstrip('\n ')
    parts.append(text)
    for part in parts:
        vk.messages.send(peer_id=peer_id, message=part.strip(), random_id=0)
        time.sleep(0.3)

# ====================== ИНИЦИАЛИЗАЦИЯ ======================
dnd_sessions = load_sessions()
vk_session = vk_api.VkApi(token=VK_TOKEN)
longpoll = VkBotLongPoll(vk_session, GROUP_ID)
vk = vk_session.get_api()

# Проверка наличия файла с правилами
if not os.path.exists(RULES_FILE):
    print(f"ОШИБКА: Не найден файл {RULES_FILE}! Создай его и вставь правила DM.")
    exit(1)

# ====================== ОСНОВНОЙ ЦИКЛ ======================
for event in longpoll.listen():
    if event.type != VkBotEventType.MESSAGE_NEW:
        continue
    msg = event.obj.message
    user_id = msg['from_id']
    peer_id = msg['peer_id']
    text = msg['text'].strip()

    try:
        user_info = vk.users.get(user_ids=user_id)[0]
        user_name = user_info['first_name']
    except Exception:
        user_name = "Игрок"

    if peer_id <= 2000000000:
        send_long_message(peer_id, f'{user_name}, пиши в групповой чат!')
        continue

    # === Инициализация сессии ===
    if peer_id not in dnd_sessions:
        dnd_sessions[peer_id] = {
            'is_active': False,
            'history': [],
            'plot': None,
            'current_stage': 0
        }
        save_sessions()
    session = dnd_sessions[peer_id]

    # ==================== КОМАНДЫ ====================
    if text.lower() == '/status':
        if not session['is_active']:
            send_long_message(peer_id, "Нет активной сессии. Напиши **/dnd начать**.")
        else:
            plot = session['plot']
            stage = plot['stages'][session['current_stage']]['name']
            send_long_message(
                peer_id,
                f"Сессия D&D:\n"
                f"• Приключение: *{plot['title']}*\n"
                f"• Этап: {stage}\n"
                f"• Сообщений: {len(session['history'])}\n\n"
                "/reset — завершить"
            )
        continue

    if text.lower() == '/dnd начать':
        if session['is_active']:
            send_long_message(peer_id, f"Сессия уже идёт: *{session['plot']['title']}*. Сначала /reset")
            continue

        # === Генерация сюжета ===
        plot_prompt = """
        Ты — генератор сюжета D&D 5e.
        ВЫВОДИ ТОЛЬКО ВАЛИДНЫЙ JSON. БЕЗ ```json, БЕЗ ПОЯСНЕНИЙ.
        Формат:
        {
            "title": "Короткое название",
            "stages": [
                {"name": "Этап 1", "description": "Внутреннее описание"},
                {"name": "Этап 2", "description": "..."}
            ],
            "key_points": ["Подсказка 1", "Подсказка 2"]
        }
        """
        def generate_plot():
            try:
                model = genai.GenerativeModel('gemini-2.5-flash')
                response = model.generate_content(
                    plot_prompt,
                    generation_config=genai.types.GenerationConfig(response_mime_type="application/json")
                )
                raw = re.sub(r'^```json\s*|\s*```$', '', response.text.strip(), flags=re.MULTILINE)
                return json.loads(raw) if raw else None
            except Exception as e:
                print(f"Ошибка генерации: {e}")
                return None

        plot = None
        for _ in range(3):
            plot = generate_plot()
            if plot: break
            time.sleep(1)
        if not plot:
            send_long_message(peer_id, "Не удалось создать сюжет. Попробуй позже.")
            continue

        # === Старт сессии ===
        session.update({
            'plot': plot,
            'current_stage': 0,
            'is_active': True,
            'history': []
        })

        # Инициализируем историю
        init_msg = f"Сессия начата. Приключение: {plot['title']}. Опиши персонажа или действуй."
        session['history'].append({'role': 'model', 'parts': [init_msg]})
        save_history_to_file(peer_id, session['history'])
        save_sessions()

        send_long_message(peer_id, f"Сессия начата!\nПриключение: *{plot['title']}*\n\n{init_msg}")
        continue

    if text.lower() == '/reset':
        dnd_sessions[peer_id] = {'is_active': False, 'history': [], 'plot': None, 'current_stage': 0}
        hist_file = get_history_file(peer_id)
        if os.path.exists(hist_file):
            os.remove(hist_file)
        save_sessions()
        send_long_message(peer_id, "Сессия завершена. История удалена.")
        continue

    # ==================== D&D РЕЖИМ ====================
    if session['is_active'] and not text.startswith('/'):
        user_msg = f"{user_name}: {text}"
        session['history'].append({'role': 'user', 'parts': [user_msg]})

        # Сохраняем историю в файл
        history_file_path = save_history_to_file(peer_id, session['history'])

        # Загружаем файлы
        try:
            rules_file = genai.upload_file(RULES_FILE)
            history_file = genai.upload_file(history_file_path)

            # Формируем запрос: правила + история + текущий ввод
            model = genai.GenerativeModel('gemini-2.5-flash')

            response = model.generate_content([
                rules_file,
                history_file,
                f"Текущий сюжет (внутренний): {json.dumps(session['plot'], ensure_ascii=False)}",
                f"Текущий этап: {session['plot']['stages'][session['current_stage']]['name']}",
                f"Ключевые подсказки: {', '.join(session['plot']['key_points'])}",
                f"ОТВЕЧАЙ КРАТКО (до 3000 символов). Продолжай как DM. В первом файле правила, во втором история. Сейчас игрок сказал: {text}"
            ],
            safety_settings=[
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            ])

            ai_answer = response.candidates[0].content.parts[0].text if response.candidates else "Ошибка ответа."

            # Сохраняем ответ
            session['history'].append({'role': 'model', 'parts': [ai_answer]})
            save_history_to_file(peer_id, session['history'])
            save_sessions()

            # Авто-переход по этапам
            if any(word in ai_answer.lower() for word in ['переход', 'следующий этап', 'кульминация', 'финал']):
                if session['current_stage'] < len(session['plot']['stages']) - 1:
                    session['current_stage'] += 1
                    ai_answer += f"\n\n[Переход на этап: {session['plot']['stages'][session['current_stage']]['name']}]"

            # Броски
            if re.search(r'd(\d+)', text.lower()):
                dice = int(re.search(r'd(\d+)', text.lower()).group(1))
                roll = roll_dice(dice)
                ai_answer += f"\n\n{user_name}, d{dice}: **{roll}**"

            send_long_message(peer_id, ai_answer)

        except Exception as e:
            send_long_message(peer_id, f"Ошибка Gemini: {str(e)}")
        continue

    # ==================== ОБЫЧНЫЙ РЕЖИМ (/команда) ====================
    if text.startswith('/'):
        query = text[1:].strip()
        if not query:
            send_long_message(peer_id, f'Привет, {user_name}! Напиши / и действие.')
            continue

        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content(
                f"Ты — ассистент. Отвечай на русском. Игрок: {user_name}\nЗапрос: {query}",
                generation_config=genai.types.GenerationConfig(max_output_tokens=1000)
            )
            ai_answer = response.text

            if any(k in query.lower() for k in ['брось', 'кубик', 'd20', 'd6']):
                roll = roll_dice(20)
                ai_answer += f'\n\n{user_name}, d20: **{roll}**'

            send_long_message(peer_id, ai_answer)
        except Exception as e:
            friendly = "Ключ не работает!" if '401' in str(e) else "Лимит! Подожди минуту." if '429' in str(e) else "Ошибка бота."
            send_long_message(peer_id, f"{user_name}, {friendly}")