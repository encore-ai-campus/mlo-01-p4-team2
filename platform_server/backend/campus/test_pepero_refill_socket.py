from tests.factories import create_profile
from eventing.catalog import class_group_uuid
from unittest.mock import patch
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase, override_settings
from accounts.models import Profile
from . import consumers
from .motion import world

@override_settings(ALLOWED_HOSTS=['127.0.0.1','testserver'],KAFKA_BOOTSTRAP_SERVERS='')
class RefillSocketTests(TransactionTestCase):
    def setUp(self):
        self.box=next(o for o in world('campus').objects.values() if o.get('action')=='pepero_refill')
        x,y=self.box['interactionPoint']
        user=get_user_model().objects.create_user(username='refill-socket-check')
        create_profile(user=user,nickname='리필검증',cohort_id=class_group_uuid('mlo-1'),setup_complete=True,
                               last_position={'floor':'campus','x':x,'y':y,'dir':'right'})
        client=Client();client.force_login(user)
        self.cookie=f'{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}'
        self.room=consumers.CampusRoom()
        replacement=patch.object(consumers,'room',self.room);replacement.start();self.addCleanup(replacement.stop)

    async def test_refill_ack_status_and_character_state_all_contain_ten(self):
        from config.asgi import application
        socket=WebsocketCommunicator(application,'/ws/campus/?control=server',headers=[
            (b'origin',b'http://127.0.0.1'),(b'host',b'127.0.0.1'),(b'cookie',self.cookie.encode())])
        try:
            self.assertTrue((await socket.connect())[0])
            while True:
                message=await socket.receive_json_from(timeout=5)
                if message['type']=='control_state':break
            await socket.send_json_to({'type':'control','requestId':'refill-check','action':'pepero_refill','data':{'objectId':self.box['id']}})
            status=state=ack=None
            for _ in range(30):
                message=await socket.receive_json_from(timeout=5)
                if message['type']=='pepero_status':status=message
                if message['type']=='control_state':state=message['state']
                if message['type']=='control_result' and message['requestId']=='refill-check':ack=message;break
            self.assertEqual(ack.get('result',{}).get('remaining'),10,ack)
            self.assertEqual(status['remaining'],10)
            self.assertEqual(state['main']['peperoRemaining'],10)
        finally:
            await socket.disconnect()
