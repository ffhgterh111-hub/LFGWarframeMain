"""
Отдельный HTTP сервер для health check и самопинга
"""
from aiohttp import web
import aiohttp
import asyncio
import logging
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HealthServer:
    def __init__(self, host='0.0.0.0', port=8080):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.setup_routes()
        self.runner = None
        self.site = None
        self.last_ping_time = None
        self.ping_task = None
        
    def setup_routes(self):
        """Настройка маршрутов HTTP сервера"""
        self.app.router.add_get('/', self.handle_root)
        self.app.router.add_get('/health', self.handle_health)
        self.app.router.add_get('/status', self.handle_status)
        self.app.router.add_get('/ping-self', self.handle_ping_self)
        
    async def handle_root(self, request):
        """Главная страница"""
        return web.Response(
            text='Warframe LFG Bot is running!\n'
                 'Endpoints:\n'
                 '- /health - Health check\n'
                 '- /status - Bot status\n'
                 '- /ping-self - Ping self to keep alive'
        )
    
    async def handle_health(self, request):
        """Health check endpoint для Render"""
        return web.Response(
            text='OK',
            headers={'Content-Type': 'text/plain'}
        )
    
    async def handle_status(self, request):
        """Статус бота"""
        status = {
            'status': 'running',
            'timestamp': datetime.now().isoformat(),
            'last_ping': self.last_ping_time.isoformat() if self.last_ping_time else None,
            'render_url': os.getenv('RENDER_URL', 'Not set')
        }
        return web.json_response(status)
    
    async def handle_ping_self(self, request):
        """Пинг самого себя для предотвращения сна"""
        render_url = os.getenv('RENDER_URL')
        if not render_url:
            return web.json_response({'error': 'RENDER_URL not set'}, status=400)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'{render_url}/health', timeout=10) as response:
                    if response.status == 200:
                        self.last_ping_time = datetime.now()
                        return web.json_response({
                            'success': True,
                            'message': f'Successfully pinged {render_url}',
                            'timestamp': self.last_ping_time.isoformat()
                        })
                    else:
                        return web.json_response({
                            'success': False,
                            'message': f'Failed to ping: Status {response.status}'
                        }, status=500)
        except Exception as e:
            logger.error(f"Failed to ping self: {e}")
            return web.json_response({
                'success': False,
                'message': f'Failed to ping: {str(e)}'
            }, status=500)
    
    async def start_auto_ping(self):
        """Автоматический пинг каждые 5 минут"""
        render_url = os.getenv('RENDER_URL')
        if not render_url:
            logger.warning("RENDER_URL not set, auto-ping disabled")
            return
        
        logger.info(f"Starting auto-ping to {render_url}")
        
        while True:
            try:
                # Ждем 5 минут
                await asyncio.sleep(300)  # 5 минут = 300 секунд
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(f'{render_url}/health', timeout=10) as response:
                        if response.status == 200:
                            self.last_ping_time = datetime.now()
                            logger.info(f"Auto-ping successful at {self.last_ping_time}")
                        else:
                            logger.warning(f"Auto-ping failed: Status {response.status}")
                            
            except asyncio.CancelledError:
                logger.info("Auto-ping task cancelled")
                break
            except Exception as e:
                logger.error(f"Auto-ping error: {e}")
                await asyncio.sleep(60)  # Подождать минуту при ошибке
    
    async def start(self):
        """Запуск HTTP сервера"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        
        # Запускаем задачу авто-пинга
        self.ping_task = asyncio.create_task(self.start_auto_ping())
        
        logger.info(f"Health server started on http://{self.host}:{self.port}")
    
    async def stop(self):
        """Остановка HTTP сервера"""
        if self.ping_task:
            self.ping_task.cancel()
            try:
                await self.ping_task
            except asyncio.CancelledError:
                pass
        
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        
        logger.info("Health server stopped")

# Синглтон экземпляр
health_server = HealthServer()