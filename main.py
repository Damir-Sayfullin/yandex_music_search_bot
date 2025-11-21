import os
import logging
import threading
import time
import requests
from aiohttp import web
import asyncio
import psycopg2
import json
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from yandex_music import Client

# Moscow timezone
MSK = pytz.timezone('Europe/Moscow')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

yandex_client = None
db_connection = None

def get_db_connection():
    try:
        conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        return conn
    except Exception as e:
        logger.error(f'Database connection error: {e}')
        return None

def log_user(user_id, username, first_name, last_name):
    try:
        conn = get_db_connection()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO users (user_id, username, first_name, last_name, total_uses) VALUES (%s, %s, %s, %s, 1) '
            'ON CONFLICT (user_id) DO UPDATE SET total_uses = users.total_uses + 1',
            (user_id, username, first_name, last_name)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f'Error logging user: {e}')

def log_search(user_id, query, results_count):
    try:
        conn = get_db_connection()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute(
            'UPDATE users SET total_searches = total_searches + 1 WHERE user_id = %s',
            (user_id,)
        )
        cur.execute(
            'INSERT INTO searches (user_id, query, results_count) VALUES (%s, %s, %s)',
            (user_id, query, results_count)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f'Error logging search: {e}')

def log_action(user_id, action_type, action_details=None):
    """Log user action to user_actions table"""
    try:
        conn = get_db_connection()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO user_actions (user_id, action_type, action_details) VALUES (%s, %s, %s)',
            (user_id, action_type, action_details)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f'Error logging action: {e}')

def log_track_view(user_id, track_title, track_artists, query):
    try:
        conn = get_db_connection()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO track_views (user_id, track_title, track_artists, query) VALUES (%s, %s, %s, %s)',
            (user_id, track_title, track_artists, query)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f'Error logging track view: {e}')

def log_bot_startup():
    try:
        conn = get_db_connection()
        if not conn:
            return
        cur = conn.cursor()
        # Get current UTC time from Python and store as ISO string
        utc_now = datetime.now(pytz.UTC)
        cur.execute(
            "INSERT INTO bot_sessions (started_at) VALUES (%s)",
            (utc_now,)
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info('Bot startup logged to database')
    except Exception as e:
        logger.error(f'Error logging bot startup: {e}')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    log_user(user.id, user.username, user.first_name, user.last_name)
    log_action(user.id, 'команда /start')
    
    await update.message.reply_text(
        '🎵 Привет! Я бот для поиска музыки в Яндекс.Музыке\n\n'
        'Отправьте мне название трека или исполнителя, и я найду музыку для вас!\n\n'
        'Используйте /help чтобы увидеть доступные команды.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_is_admin = is_admin(user_id)
    
    log_action(user_id, 'команда /help')
    
    help_text = "🎵 Доступные команды:\n\n"
    help_text += "/start - Приветственное сообщение\n"
    help_text += "/search <название> - Поиск в Яндекс.Музыке (10 результатов)\n"
    help_text += "/my_stats - Ваша личная статистика\n"
    help_text += "/help - Показать это сообщение\n"
    
    if user_is_admin:
        help_text += "\n👑 АДМИНИСТРАТОР:\n"
        help_text += "/admin_stats - Общая статистика бота\n"
        help_text += "/bot_uptime - Время запуска и работа бота (МСК)\n"
        help_text += "/user_actions <user_id или @username> - Действия пользователя\n"
        help_text += "/list_users - Список всех пользователей и ролей\n"
        help_text += "/add_admin <user_id или @username> - Добавить администратора\n"
        help_text += "/remove_admin <user_id или @username> - Удалить администратора\n"
    
    help_text += "\nПросто отправьте название трека или исполнителя, и я найду музыку!\n\n"
    help_text += "Примеры:\n"
    help_text += "• Imagine Dragons\n"
    help_text += "• Believer\n"
    help_text += "• Metallica - Nothing Else Matters"
    
    await update.message.reply_text(help_text)

async def search_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global yandex_client
    
    user = update.message.from_user
    log_user(user.id, user.username, user.first_name, user.last_name)
    
    query = ' '.join(context.args) if context.args else None
    
    if not query:
        await update.message.reply_text(
            'Пожалуйста, укажите что искать:\n'
            '/search Название трека или исполнителя'
        )
        return
    
    try:
        if not yandex_client:
            await update.message.reply_text('❌ Яндекс.Музыка не настроена.')
            return
        
        await update.message.reply_text(f'🔍 Ищу: {query}...')
        
        search_result = yandex_client.search(query, type_='track')
        
        if not search_result or not search_result.tracks:
            await update.message.reply_text('❌ Ничего не найдено. Попробуйте другой запрос.')
            return
        
        tracks = search_result.tracks.results[:10]
        log_search(user.id, query, len(tracks))
        log_action(user.id, 'поиск /search', query)
        
        response = f'🎵 Найдено: {len(tracks)} треков\n\n'
        
        for i, track in enumerate(tracks, 1):
            artists = ', '.join([artist.name for artist in track.artists])
            duration_seconds = track.duration_ms // 1000 if track.duration_ms else 0
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            
            log_track_view(user.id, track.title, artists, query)
            
            response += f'{i}. {artists} - {track.title}\n'
            response += f'   ⏱ {minutes}:{seconds:02d}\n'
            
            if track.albums and len(track.albums) > 0:
                album_id = track.albums[0].id
                track_id = track.id
                track_url = f'https://music.yandex.ru/album/{album_id}/track/{track_id}'
                response += f'   🔗 {track_url}\n'
            
            response += '\n'
        
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f'Ошибка поиска: {e}')
        await update.message.reply_text(f'❌ Ошибка при поиске: {str(e)}')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global yandex_client
    
    user = update.message.from_user
    log_user(user.id, user.username, user.first_name, user.last_name)
    log_action(user.id, 'поиск (текст)', update.message.text)
    
    if not yandex_client:
        await update.message.reply_text('❌ Яндекс.Музыка не настроена.')
        return
    
    query = update.message.text
    
    try:
        await update.message.reply_text(f'🔍 Ищу: {query}...')
        
        search_result = yandex_client.search(query, type_='track')
        
        if not search_result or not search_result.tracks:
            await update.message.reply_text('❌ Ничего не найдено. Попробуйте другой запрос.')
            return
        
        tracks = search_result.tracks.results[:10]
        log_search(user.id, query, len(tracks))
        
        response = f'🎵 Найдено: {len(tracks)} треков\n\n'
        
        for i, track in enumerate(tracks, 1):
            artists = ', '.join([artist.name for artist in track.artists])
            duration_seconds = track.duration_ms // 1000 if track.duration_ms else 0
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            
            log_track_view(user.id, track.title, artists, query)
            
            response += f'{i}. {artists} - {track.title}\n'
            response += f'   ⏱ {minutes}:{seconds:02d}\n'
            
            if track.albums and len(track.albums) > 0:
                album_id = track.albums[0].id
                track_id = track.id
                track_url = f'https://music.yandex.ru/album/{album_id}/track/{track_id}'
                response += f'   🔗 {track_url}\n'
            
            response += '\n'
        
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f'Ошибка поиска: {e}')
        await update.message.reply_text(f'❌ Ошибка при поиске: {str(e)}')

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    log_user(user.id, user.username, user.first_name, user.last_name)
    
    unknown_cmd = update.message.text.split()[0] if update.message.text else ''
    
    response = f'❌ Неизвестная команда: {unknown_cmd}\n\n'
    response += '🎵 Доступные команды:\n\n'
    response += '/start - Приветственное сообщение\n'
    response += '/search <название> - Поиск трека в Яндекс.Музыке\n'
    response += '/my_stats - Ваша личная статистика\n'
    response += '/help - Показать все команды\n\n'
    response += 'Просто отправьте название трека, и я найду музыку!'
    
    await update.message.reply_text(response)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f'Update {update} caused error {context.error}')

def get_user_id_by_username(username):
    """Get user_id by username (with or without @)"""
    try:
        if username.startswith('@'):
            username = username[1:]
        
        conn = get_db_connection()
        if not conn:
            return None
        cur = conn.cursor()
        
        cur.execute("SELECT user_id FROM users WHERE username = %s", (username,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        return result[0] if result else None
    except Exception as e:
        logger.error(f'Error getting user_id by username: {e}')
        return None

def is_admin(user_id):
    """Check if user is admin (from DB or env var)"""
    main_admin_id = os.getenv('ADMIN_USER_ID')
    if main_admin_id and int(main_admin_id) == user_id:
        return True
    
    try:
        conn = get_db_connection()
        if not conn:
            return False
        cur = conn.cursor()
        
        cur.execute("SELECT user_id FROM admins WHERE user_id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        return result is not None
    except Exception as e:
        logger.error(f'Error checking admin status: {e}')
        return False

def add_admin_to_db(target_user_id, added_by_user_id):
    """Add user to admins table"""
    try:
        conn = get_db_connection()
        if not conn:
            return False
        cur = conn.cursor()
        
        cur.execute(
            "INSERT INTO admins (user_id, added_by) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (target_user_id, added_by_user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f'Error adding admin: {e}')
        return False

def remove_admin_from_db(target_user_id):
    """Remove user from admins table"""
    try:
        conn = get_db_connection()
        if not conn:
            return False
        cur = conn.cursor()
        
        cur.execute("DELETE FROM admins WHERE user_id = %s", (target_user_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f'Error removing admin: {e}')
        return False

def get_all_users():
    """Get all users with their roles"""
    try:
        conn = get_db_connection()
        if not conn:
            return None
        cur = conn.cursor()
        main_admin_id = os.getenv('ADMIN_USER_ID')
        
        cur.execute("""
            SELECT user_id, username, first_name, total_uses, total_searches, created_at
            FROM users
            ORDER BY total_uses DESC
        """)
        users = cur.fetchall()
        
        # Get all admins from DB
        cur.execute("SELECT user_id FROM admins")
        admin_ids = set(row[0] for row in cur.fetchall())
        
        cur.close()
        conn.close()
        
        users_with_roles = []
        for user in users:
            user_id = user[0]
            if main_admin_id and int(main_admin_id) == user_id:
                role = 'Главный админ 👑'
            elif user_id in admin_ids:
                role = 'Админ 🔑'
            else:
                role = 'Пользователь 👤'
            users_with_roles.append((user, role))
        
        return users_with_roles
    except Exception as e:
        logger.error(f'Error getting all users: {e}')
        return None

def get_user_actions(user_id, limit=50):
    """Get user actions with timestamps"""
    try:
        conn = get_db_connection()
        if not conn:
            return None
        cur = conn.cursor()
        
        cur.execute("""
            SELECT username, first_name, total_uses, total_searches
            FROM users
            WHERE user_id = %s
        """, (user_id,))
        user_info = cur.fetchone()
        
        if not user_info:
            return None
        
        cur.execute("""
            SELECT action_type, action_details, created_at
            FROM user_actions
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (user_id, limit))
        
        actions = cur.fetchall()
        cur.close()
        conn.close()
        
        return {
            'user_info': user_info,
            'actions': actions
        }
    except Exception as e:
        logger.error(f'Error getting user actions: {e}')
        return None

def get_bot_uptime():
    """Get bot startup time and calculate uptime"""
    try:
        conn = get_db_connection()
        if not conn:
            return None
        cur = conn.cursor()
        
        cur.execute("""
            SELECT started_at 
            FROM bot_sessions 
            ORDER BY started_at DESC LIMIT 1
        """)
        session_result = cur.fetchone()
        cur.close()
        conn.close()
        
        if not session_result:
            return None
        
        utc_time = session_result[0]
        if utc_time.tzinfo is None:
            utc_time = pytz.UTC.localize(utc_time)
        msk_time = utc_time.astimezone(MSK)
        
        return {'started_at': msk_time}
    except Exception as e:
        logger.error(f'Error getting bot uptime: {e}')
        return None

def get_admin_stats():
    try:
        conn = get_db_connection()
        if not conn:
            return None
        cur = conn.cursor()
        
        stats = {}
        
        cur.execute('SELECT COUNT(*) FROM users')
        stats['total_users'] = cur.fetchone()[0]
        
        cur.execute('SELECT SUM(total_searches) FROM users')
        stats['total_searches'] = cur.fetchone()[0] or 0
        
        cur.execute('SELECT SUM(total_uses) FROM users')
        stats['total_uses'] = cur.fetchone()[0] or 0
        
        cur.execute('SELECT COUNT(*) FROM track_views')
        stats['total_track_views'] = cur.fetchone()[0]
        
        cur.execute('SELECT COUNT(DISTINCT query) FROM searches')
        stats['unique_searches'] = cur.fetchone()[0]
        
        if stats['total_users'] > 0:
            stats['avg_searches_per_user'] = round(stats['total_searches'] / stats['total_users'], 2)
        else:
            stats['avg_searches_per_user'] = 0
        
        if stats['total_searches'] > 0:
            stats['avg_views_per_search'] = round(stats['total_track_views'] / stats['total_searches'], 2)
        else:
            stats['avg_views_per_search'] = 0
        
        cur.execute('SELECT COUNT(*) FROM users WHERE total_searches >= 5')
        stats['active_users'] = cur.fetchone()[0]
        
        # Get top 10 users with their last interaction info
        cur.execute("""
            SELECT u.user_id, u.username, u.first_name, u.total_uses, u.total_searches,
                   ua.created_at as last_interaction,
                   ua.action_type,
                   ua.action_details
            FROM users u
            LEFT JOIN LATERAL (
                SELECT action_type, action_details, created_at
                FROM user_actions
                WHERE user_id = u.user_id
                ORDER BY created_at DESC
                LIMIT 1
            ) ua ON true
            ORDER BY u.total_uses DESC 
            LIMIT 10
        """)
        stats['top_users'] = cur.fetchall()
        
        # Get last search query for each top user
        top_user_ids = [user[0] for user in stats['top_users']]
        stats['user_last_searches'] = {}
        for uid in top_user_ids:
            cur.execute("""
                SELECT query FROM searches 
                WHERE user_id = %s 
                ORDER BY created_at DESC 
                LIMIT 1
            """, (uid,))
            result = cur.fetchone()
            stats['user_last_searches'][uid] = result[0] if result else None
        
        cur.execute("""
            SELECT query, COUNT(*) as count 
            FROM searches 
            GROUP BY query 
            ORDER BY count DESC 
            LIMIT 10
        """)
        stats['popular_queries'] = cur.fetchall()
        
        cur.execute("""
            SELECT track_artists, COUNT(*) as count 
            FROM track_views 
            WHERE track_artists IS NOT NULL AND track_artists != ''
            GROUP BY track_artists 
            ORDER BY count DESC 
            LIMIT 5
        """)
        stats['popular_artists'] = cur.fetchall()
        
        cur.close()
        conn.close()
        return stats
    except Exception as e:
        logger.error(f'Error getting admin stats: {e}')
        return None

async def list_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text('❌ У вас нет доступа к этой команде.')
        logger.warning(f'Unauthorized list_users access attempt by user {user_id}')
        return
    
    log_action(user_id, 'команда /list_users')
    
    users = get_all_users()
    if not users:
        await update.message.reply_text('❌ Ошибка при получении списка пользователей.')
        return
    
    response = f'👥 СПИСОК ВСЕХ ПОЛЬЗОВАТЕЛЕЙ ({len(users)})\n\n'
    response += '='*50 + '\n\n'
    
    for i, (user_data, role) in enumerate(users, 1):
        uid = user_data[0]
        username = user_data[1]
        first_name = user_data[2]
        total_uses = user_data[3]
        total_searches = user_data[4]
        
        username_str = f'@{username}' if username else first_name
        response += f'{i}. {username_str}\n'
        response += f'   ID: {uid}\n'
        response += f'   Роль: {role}\n'
        response += f'   Взаимодействий: {total_uses} | Поисков: {total_searches}\n\n'
    
    await update.message.reply_text(response)
    logger.info(f'List users requested by admin {user_id}')

async def user_actions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text('❌ У вас нет доступа к этой команде.')
        logger.warning(f'Unauthorized user_actions access attempt by user {user_id}')
        return
    
    if not context.args:
        await update.message.reply_text('Использование: /user_actions <user_id или @username>')
        return
    
    arg = context.args[0]
    target_user_id = None
    
    # Try to parse as user_id first
    try:
        target_user_id = int(arg)
    except ValueError:
        # Try to parse as username
        target_user_id = get_user_id_by_username(arg)
        if not target_user_id:
            await update.message.reply_text('❌ Пользователь не найден.')
            return
    
    user_actions = get_user_actions(target_user_id, limit=30)
    if not user_actions:
        await update.message.reply_text('❌ Пользователь не найден.')
        return
    
    username, first_name, total_uses, total_searches = user_actions['user_info']
    actions = user_actions['actions']
    
    username_str = f'@{username}' if username else first_name
    response = f'📊 ДЕЙСТВИЯ ПОЛЬЗОВАТЕЛЯ: {username_str}\n\n'
    response += f'💬 Всего взаимодействий: {total_uses}\n'
    response += f'🔍 Всего поисков: {total_searches}\n\n'
    response += '='*50 + '\n'
    response += '📝 ПОСЛЕДНИЕ ДЕЙСТВИЯ (МСК, макс. 30):\n\n'
    
    for i, (action_type, action_details, created_at) in enumerate(actions, 1):
        if created_at.tzinfo is None:
            created_at_utc = pytz.UTC.localize(created_at)
        else:
            created_at_utc = created_at
        created_at_msk = created_at_utc.astimezone(MSK)
        time_str = created_at_msk.strftime("%d.%m.%Y %H:%M:%S")
        
        response += f'{i}. {time_str} - {action_type}'
        if action_details:
            response += f': "{action_details}"'
        response += '\n'
    
    await update.message.reply_text(response)
    logger.info(f'User actions for {target_user_id} requested by admin {user_id}')

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text('❌ У вас нет доступа к этой команде.')
        logger.warning(f'Unauthorized add_admin access attempt by user {user_id}')
        return
    
    if not context.args:
        await update.message.reply_text('Использование: /add_admin <user_id или @username>')
        return
    
    arg = context.args[0]
    target_user_id = None
    
    try:
        target_user_id = int(arg)
    except ValueError:
        target_user_id = get_user_id_by_username(arg)
        if not target_user_id:
            await update.message.reply_text('❌ Пользователь не найден.')
            return
    
    log_action(user_id, 'команда /add_admin', str(target_user_id))
    
    if add_admin_to_db(target_user_id, user_id):
        await update.message.reply_text(f'✅ Пользователь {target_user_id} добавлен в админы.')
        logger.info(f'User {target_user_id} added to admins by {user_id}')
    else:
        await update.message.reply_text('❌ Ошибка при добавлении админа.')

async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    main_admin_id = os.getenv('ADMIN_USER_ID')
    
    if not is_admin(user_id):
        await update.message.reply_text('❌ У вас нет доступа к этой команде.')
        logger.warning(f'Unauthorized remove_admin access attempt by user {user_id}')
        return
    
    if not context.args:
        await update.message.reply_text('Использование: /remove_admin <user_id или @username>')
        return
    
    arg = context.args[0]
    target_user_id = None
    
    try:
        target_user_id = int(arg)
    except ValueError:
        target_user_id = get_user_id_by_username(arg)
        if not target_user_id:
            await update.message.reply_text('❌ Пользователь не найден.')
            return
    
    log_action(user_id, 'команда /remove_admin', str(target_user_id))
    
    # Prevent removing main admin
    if main_admin_id and int(main_admin_id) == target_user_id:
        await update.message.reply_text('❌ Нельзя удалить главного администратора!')
        return
    
    if remove_admin_from_db(target_user_id):
        await update.message.reply_text(f'✅ Пользователь {target_user_id} удален из админов.')
        logger.info(f'User {target_user_id} removed from admins by {user_id}')
    else:
        await update.message.reply_text('❌ Ошибка при удалении админа.')

async def bot_uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text('❌ У вас нет доступа к этой команде.')
        logger.warning(f'Unauthorized bot_uptime access attempt by user {user_id}')
        return
    
    log_action(user_id, 'команда /bot_uptime')
    
    uptime_data = get_bot_uptime()
    if not uptime_data:
        await update.message.reply_text('❌ Ошибка при получении информации о боте.')
        return
    
    started_at = uptime_data['started_at']
    now_msk = datetime.now(MSK)
    uptime = now_msk - started_at
    
    days = uptime.days
    hours = uptime.seconds // 3600
    minutes = (uptime.seconds % 3600) // 60
    seconds = uptime.seconds % 60
    
    response = '⏱ ИНФОРМАЦИЯ О БОТЕ (МСК)\n\n'
    response += f'🔄 Время запуска: {started_at.strftime("%d.%m.%Y %H:%M:%S")}\n'
    response += f'⌛ Время работы: {days}д {hours}ч {minutes}м {seconds}с'
    
    await update.message.reply_text(response)
    logger.info(f'Bot uptime requested by user {user_id}')

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text('❌ У вас нет доступа к этой команде.')
        logger.warning(f'Unauthorized admin access attempt by user {user_id}')
        return
    
    log_action(user_id, 'команда /admin_stats')
    
    stats = get_admin_stats()
    if not stats:
        await update.message.reply_text('❌ Ошибка при получении статистики.')
        return
    
    response = '📊 ОБЩАЯ СТАТИСТИКА БОТА\n\n'
    response += '📈 Ключевые метрики:\n'
    response += f'👥 Всего пользователей: {stats["total_users"]}\n'
    response += f'🔍 Всего поисков: {stats["total_searches"]}\n'
    response += f'💬 Всего взаимодействий: {stats["total_uses"]}\n'
    response += f'📊 Средне поисков/пользователя: {stats["avg_searches_per_user"]}\n'
    response += '\n' + '='*50 + '\n\n'
    
    response += '🏆 ТОП 10 АКТИВНЫХ ПОЛЬЗОВАТЕЛЕЙ:\n'
    for i, user_data in enumerate(stats['top_users'], 1):
        uid = user_data[0]
        username = user_data[1]
        first_name = user_data[2]
        uses = user_data[3]
        searches = user_data[4]
        last_interaction = user_data[5]
        action_type = user_data[6]
        query_text = user_data[7]
        
        username_str = f'@{username}' if username else f'{first_name}'
        response += f'{i}. {username_str}\n'
        response += f'   💬 {uses} взаимодействий | 🔍 {searches} поисков\n'
        
        # Format last interaction time
        if last_interaction:
            if last_interaction.tzinfo is None:
                last_interaction_utc = pytz.UTC.localize(last_interaction)
            else:
                last_interaction_utc = last_interaction
            last_interaction_msk = last_interaction_utc.astimezone(MSK)
            last_interaction_str = last_interaction_msk.strftime("%d.%m.%Y %H:%M")
        else:
            last_interaction_str = "неизвестно"
        
        response += f'   📅 Последнее: {last_interaction_str}'
        if action_type:
            response += f' ({action_type})'
        response += '\n'
        
        # Add last search query if available
        last_search = stats['user_last_searches'].get(uid)
        if last_search:
            response += f'   🔍 Последний поиск: "{last_search}"\n'
        
        response += '\n'
    
    response += '='*50 + '\n\n'
    response += '🔥 ТОП 10 ПОПУЛЯРНЫХ ЗАПРОСОВ:\n'
    for i, (query, count) in enumerate(stats['popular_queries'], 1):
        response += f'{i}. "{query}" - {count} поиск(ов)\n'
    
    if stats['popular_artists']:
        response += '\n' + '='*50 + '\n\n'
        response += '⭐ ТОП 5 ПОПУЛЯРНЫХ ИСПОЛНИТЕЛЕЙ:\n'
        for i, (artist, count) in enumerate(stats['popular_artists'], 1):
            response += f'{i}. {artist} - {count} просмотров\n'
    
    await update.message.reply_text(response)
    logger.info(f'Admin stats requested by user {user_id}')

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    
    log_action(user_id, 'команда /my_stats')
    
    try:
        conn = get_db_connection()
        if not conn:
            await update.message.reply_text('❌ Ошибка подключения к базе данных.')
            return
        
        cur = conn.cursor()
        
        cur.execute("""
            SELECT username, first_name, total_uses, total_searches, created_at 
            FROM users 
            WHERE user_id = %s
        """, (user_id,))
        result = cur.fetchone()
        
        if not result:
            await update.message.reply_text('❌ Ваши данные не найдены.')
            cur.close()
            conn.close()
            return
        
        username, first_name, total_uses, total_searches, created_at = result
        
        # Top queries
        cur.execute("""
            SELECT query, COUNT(*) as count 
            FROM searches 
            WHERE user_id = %s 
            GROUP BY query 
            ORDER BY count DESC 
            LIMIT 5
        """, (user_id,))
        my_queries = cur.fetchall()
        
        # Track views stats
        cur.execute("""
            SELECT COUNT(*) FROM track_views 
            WHERE user_id = %s
        """, (user_id,))
        total_track_views = cur.fetchone()[0]
        
        # Popular artists
        cur.execute("""
            SELECT track_artists, COUNT(*) as count 
            FROM track_views 
            WHERE user_id = %s AND track_artists IS NOT NULL AND track_artists != ''
            GROUP BY track_artists 
            ORDER BY count DESC 
            LIMIT 3
        """, (user_id,))
        favorite_artists = cur.fetchall()
        
        # Calculate average searches
        avg_per_session = round(total_searches / total_uses, 2) if total_uses > 0 else 0
        
        cur.close()
        conn.close()
        
        # Convert created_at to MSK
        if created_at:
            if created_at.tzinfo is None:
                created_at_utc = pytz.UTC.localize(created_at)
            else:
                created_at_utc = created_at
            created_at_msk = created_at_utc.astimezone(MSK)
            created_at_str = created_at_msk.strftime("%d.%m.%Y")
        else:
            created_at_str = "неизвестно"
        
        response = f'📊 ВАШ ПРОФИЛЬ И СТАТИСТИКА\n\n'
        response += f'👤 {first_name}\n'
        if username:
            response += f'📱 @{username}\n'
        response += f'📅 На боте с: {created_at_str}\n\n'
        
        response += '📈 АКТИВНОСТЬ:\n'
        response += f'💬 Всего взаимодействий: {total_uses}\n'
        response += f'🔍 Всего поисков: {total_searches}\n'
        response += f'🎵 Просмотров треков: {total_track_views}\n'
        
        if my_queries:
            response += f'🔥 ВАШ ТОП ЗАПРОСОВ:\n'
            for i, (query, count) in enumerate(my_queries, 1):
                response += f'{i}. "{query}" - {count} раз\n'
        
        if favorite_artists:
            response += f'\n⭐ ВАШИ ЛЮБИМЫЕ ИСПОЛНИТЕЛИ:\n'
            for i, (artist, count) in enumerate(favorite_artists, 1):
                response += f'{i}. {artist} - {count} просмотров\n'
        
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f'Error getting user stats: {e}')
        await update.message.reply_text('❌ Ошибка при получении статистики.')

async def health_check(request):
    return web.Response(text='Bot is alive!')

async def start_webserver():
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info('Web server started on port 8080')
    print('🌐 Keep-alive веб-сервер запущен на порту 8080')

def run_webserver():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_webserver())
    loop.run_forever()

def self_ping():
    logger.info('Self-ping thread started')
    print('🔄 Самопинг включен - бот будет пинговать себя каждые 5 минут')
    
    time.sleep(10)
    
    while True:
        try:
            response = requests.get('http://localhost:8080/health', timeout=10)
            if response.status_code == 200:
                logger.info('Self-ping successful')
            else:
                logger.warning(f'Self-ping returned status {response.status_code}')
        except Exception as e:
            logger.error(f'Self-ping failed: {e}')
        
        time.sleep(300)

def main():
    global yandex_client
    
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error('TELEGRAM_BOT_TOKEN not found in environment variables!')
        print('Ошибка: TELEGRAM_BOT_TOKEN не найден!')
        print('Пожалуйста, добавьте токен бота в переменные окружения.')
        return
    
    yandex_token = os.getenv('YANDEX_MUSIC_TOKEN')
    
    if yandex_token:
        try:
            yandex_client = Client(yandex_token).init()
            logger.info('Яндекс.Музыка подключена успешно!')
            print('✅ Яндекс.Музыка подключена!')
        except Exception as e:
            logger.error(f'Ошибка подключения к Яндекс.Музыке: {e}')
            print(f'⚠️ Не удалось подключиться к Яндекс.Музыке: {e}')
    else:
        logger.warning('YANDEX_MUSIC_TOKEN not found')
        print('⚠️ YANDEX_MUSIC_TOKEN не найден')
    
    webserver_thread = threading.Thread(target=run_webserver, daemon=True)
    webserver_thread.start()
    
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()
    
    # Log bot startup to database
    log_bot_startup()
    
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("search", search_music))
    application.add_handler(CommandHandler("admin_stats", admin_stats))
    application.add_handler(CommandHandler("bot_uptime", bot_uptime))
    application.add_handler(CommandHandler("user_actions", user_actions_cmd))
    application.add_handler(CommandHandler("list_users", list_users_cmd))
    application.add_handler(CommandHandler("add_admin", add_admin_cmd))
    application.add_handler(CommandHandler("remove_admin", remove_admin_cmd))
    application.add_handler(CommandHandler("my_stats", my_stats))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    application.add_error_handler(error_handler)
    
    logger.info('Бот запущен!')
    print('🤖 Бот успешно запущен и готов к работе!')
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
