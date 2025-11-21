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
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from yandex_music import Client

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

yandex_client = None
db_connection = None
zvuk_api_key = None

def search_zvuk(query):
    """Search music in Zvuk (Sber)"""
    if not zvuk_api_key:
        return None
    try:
        headers = {'X-API-Key': zvuk_api_key}
        url = 'https://api.zvuk.com/v1/search'
        params = {'q': query, 'type': 'tracks', 'limit': 10}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('data', {}).get('tracks', [])
        return None
    except Exception as e:
        logger.error(f'Zvuk search error: {e}')
        return None

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    log_user(user.id, user.username, user.first_name, user.last_name)
    
    await update.message.reply_text(
        '🎵 Привет! Я бот для поиска музыки в Яндекс.Музыке\n\n'
        'Отправьте мне название трека или исполнителя, и я найду музыку для вас!\n\n'
        'Используйте /help чтобы увидеть доступные команды.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🎵 Доступные команды:

/start - Приветственное сообщение
/search <название> - Поиск трека (10 результатов)
/my_stats - Ваша личная статистика
/help - Показать это сообщение

Просто отправьте название трека или исполнителя, и я найду музыку!

Примеры:
• Imagine Dragons
• Believer
• Metallica - Nothing Else Matters
    """
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
        await update.message.reply_text(f'🔍 Ищу: {query}...')
        
        all_tracks = []
        sources = []
        
        if yandex_client:
            try:
                search_result = yandex_client.search(query, type_='track')
                if search_result and search_result.tracks:
                    yandex_tracks = search_result.tracks.results[:10]
                    all_tracks.extend(yandex_tracks)
                    sources.append('🎵 Яндекс.Музыка')
            except Exception as e:
                logger.error(f'Yandex search error: {e}')
        
        zvuk_tracks = search_zvuk(query)
        if zvuk_tracks:
            all_tracks.extend(zvuk_tracks)
            sources.append('🔊 Звук')
        
        if not all_tracks:
            await update.message.reply_text('❌ Ничего не найдено. Попробуйте другой запрос.')
            return
        
        all_tracks = all_tracks[:10]
        log_search(user.id, query, len(all_tracks))
        
        sources_text = ' и '.join(sources) if sources else ''
        response = f'🎵 Найдено в {sources_text}: {len(all_tracks)} треков\n\n'
        
        for i, track in enumerate(all_tracks, 1):
            if isinstance(track, dict):
                track_title = track.get('title', 'Unknown')
                artists = ', '.join([a.get('name', 'Unknown') for a in track.get('artists', [])])
                duration = track.get('duration', 0)
                url = track.get('url', '')
                source = '🔊 Звук'
            else:
                track_title = track.title
                artists = ', '.join([artist.name for artist in track.artists])
                duration = track.duration_ms // 1000 if track.duration_ms else 0
                source = '🎵 Яндекс.Музыка'
                url = None
            
            duration_seconds = duration // 1000 if duration > 1000 else duration
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            
            log_track_view(user.id, track_title, artists, query)
            
            response += f'{i}. {artists} - {track_title}\n'
            response += f'   ⏱ {minutes}:{seconds:02d} {source}\n'
            
            if url and not isinstance(track, dict):
                if track.albums and len(track.albums) > 0:
                    album_id = track.albums[0].id
                    track_id = track.id
                    track_url = f'https://music.yandex.ru/album/{album_id}/track/{track_id}'
                    response += f'   💿 {track.albums[0].title}\n'
                    response += f'   🔗 {track_url}\n'
            elif isinstance(track, dict) and url:
                response += f'   🔗 {url}\n'
            
            response += '\n'
        
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f'Ошибка поиска: {e}')
        await update.message.reply_text(f'❌ Ошибка при поиске: {str(e)}')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global yandex_client
    
    user = update.message.from_user
    log_user(user.id, user.username, user.first_name, user.last_name)
    
    query = update.message.text
    
    try:
        await update.message.reply_text(f'🔍 Ищу: {query}...')
        
        all_tracks = []
        sources = []
        
        if yandex_client:
            try:
                search_result = yandex_client.search(query, type_='track')
                if search_result and search_result.tracks:
                    yandex_tracks = search_result.tracks.results[:10]
                    all_tracks.extend(yandex_tracks)
                    sources.append('🎵 Яндекс.Музыка')
            except Exception as e:
                logger.error(f'Yandex search error: {e}')
        
        zvuk_tracks = search_zvuk(query)
        if zvuk_tracks:
            all_tracks.extend(zvuk_tracks)
            sources.append('🔊 Звук')
        
        if not all_tracks:
            await update.message.reply_text('❌ Ничего не найдено. Попробуйте другой запрос.')
            return
        
        all_tracks = all_tracks[:10]
        log_search(user.id, query, len(all_tracks))
        
        sources_text = ' и '.join(sources) if sources else ''
        response = f'🎵 Найдено в {sources_text}:\n\n'
        
        for i, track in enumerate(all_tracks, 1):
            if isinstance(track, dict):
                track_title = track.get('title', 'Unknown')
                artists = ', '.join([a.get('name', 'Unknown') for a in track.get('artists', [])])
                duration = track.get('duration', 0)
                url = track.get('url', '')
                source = '🔊 Звук'
            else:
                track_title = track.title
                artists = ', '.join([artist.name for artist in track.artists])
                duration = track.duration_ms // 1000 if track.duration_ms else 0
                source = '🎵 Яндекс.Музыка'
                url = None
            
            duration_seconds = duration // 1000 if duration > 1000 else duration
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            
            log_track_view(user.id, track_title, artists, query)
            
            response += f'{i}. {artists} - {track_title}\n'
            response += f'   ⏱ {minutes}:{seconds:02d} {source}\n'
            
            if url and not isinstance(track, dict):
                if track.albums and len(track.albums) > 0:
                    album_id = track.albums[0].id
                    track_id = track.id
                    track_url = f'https://music.yandex.ru/album/{album_id}/track/{track_id}'
                    response += f'   🔗 {track_url}\n'
            elif isinstance(track, dict) and url:
                response += f'   🔗 {url}\n'
            
            response += '\n'
        
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f'Ошибка поиска: {e}')
        await update.message.reply_text(f'❌ Ошибка при поиске: {str(e)}')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f'Update {update} caused error {context.error}')

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
        
        cur.execute('SELECT COUNT(*) FROM track_views')
        stats['total_track_views'] = cur.fetchone()[0]
        
        cur.execute("""
            SELECT user_id, username, first_name, total_uses, total_searches 
            FROM users 
            ORDER BY total_uses DESC 
            LIMIT 10
        """)
        stats['top_users'] = cur.fetchall()
        
        cur.execute("""
            SELECT query, COUNT(*) as count 
            FROM searches 
            GROUP BY query 
            ORDER BY count DESC 
            LIMIT 10
        """)
        stats['popular_queries'] = cur.fetchall()
        
        cur.close()
        conn.close()
        return stats
    except Exception as e:
        logger.error(f'Error getting admin stats: {e}')
        return None

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = os.getenv('ADMIN_USER_ID')
    user_id = update.message.from_user.id
    
    if not admin_id or int(admin_id) != user_id:
        await update.message.reply_text('❌ У вас нет доступа к этой команде.')
        logger.warning(f'Unauthorized admin access attempt by user {user_id}')
        return
    
    stats = get_admin_stats()
    if not stats:
        await update.message.reply_text('❌ Ошибка при получении статистики.')
        return
    
    response = '📊 Общая статистика бота:\n\n'
    response += f'👥 Всего пользователей: {stats["total_users"]}\n'
    response += f'🔍 Всего поисков: {stats["total_searches"]}\n'
    response += f'🎵 Всего просмотров треков: {stats["total_track_views"]}\n'
    response += '\n' + '='*40 + '\n\n'
    
    response += '🏆 Топ 10 активных пользователей:\n'
    for i, (uid, username, first_name, uses, searches) in enumerate(stats['top_users'], 1):
        username_str = f'@{username}' if username else f'{first_name}'
        response += f'{i}. {username_str} - использований: {uses}, поисков: {searches}\n'
    
    response += '\n' + '='*40 + '\n\n'
    response += '🔥 Топ 10 популярных запросов:\n'
    for i, (query, count) in enumerate(stats['popular_queries'], 1):
        response += f'{i}. "{query}" - {count} поиск(ов)\n'
    
    await update.message.reply_text(response)
    logger.info(f'Admin stats requested by user {user_id}')

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    
    try:
        conn = get_db_connection()
        if not conn:
            await update.message.reply_text('❌ Ошибка подключения к базе данных.')
            return
        
        cur = conn.cursor()
        
        cur.execute("""
            SELECT username, first_name, total_uses, total_searches 
            FROM users 
            WHERE user_id = %s
        """, (user_id,))
        result = cur.fetchone()
        
        if not result:
            await update.message.reply_text('❌ Ваши данные не найдены.')
            cur.close()
            conn.close()
            return
        
        username, first_name, total_uses, total_searches = result
        
        cur.execute("""
            SELECT track_title, track_artists, created_at 
            FROM track_views 
            WHERE user_id = %s 
            ORDER BY created_at DESC 
            LIMIT 10
        """, (user_id,))
        recent_tracks = cur.fetchall()
        
        cur.execute("""
            SELECT query, COUNT(*) as count 
            FROM searches 
            WHERE user_id = %s 
            GROUP BY query 
            ORDER BY count DESC 
            LIMIT 5
        """, (user_id,))
        my_queries = cur.fetchall()
        
        cur.close()
        conn.close()
        
        response = f'📈 Ваша статистика:\n\n'
        response += f'👤 Имя: {first_name}\n'
        if username:
            response += f'📱 Юзернейм: @{username}\n'
        response += f'🔍 Всего поисков: {total_searches}\n'
        response += f'💬 Использований бота: {total_uses}\n'
        
        if my_queries:
            response += f'\n🔥 Ваши популярные запросы:\n'
            for query, count in my_queries:
                response += f'• "{query}" - {count} раз\n'
        
        if recent_tracks:
            response += f'\n🎵 Последние просмотренные треки:\n'
            for track, artists, created_at in recent_tracks[:5]:
                response += f'• {artists} - {track}\n'
        
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
    global yandex_client, zvuk_api_key
    
    zvuk_api_key = os.getenv('ZVUK_API_KEY')
    
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
    
    if zvuk_api_key:
        logger.info('Звук API ключ загружен!')
        print('✅ Звук от Сбера подключен!')
    else:
        logger.warning('ZVUK_API_KEY not found')
        print('⚠️ ZVUK_API_KEY не найден')
    
    webserver_thread = threading.Thread(target=run_webserver, daemon=True)
    webserver_thread.start()
    
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()
    
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("search", search_music))
    application.add_handler(CommandHandler("admin_stats", admin_stats))
    application.add_handler(CommandHandler("my_stats", my_stats))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    application.add_error_handler(error_handler)
    
    logger.info('Бот запущен!')
    print('🤖 Бот успешно запущен и готов к работе!')
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
