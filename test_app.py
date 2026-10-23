import hashlib
import hmac
import io
import json
import os
import tempfile
import time
import unittest
from urllib.parse import urlencode

_TMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=_TMP.name
os.environ['DEV_MODE']='true'
from fastapi.testclient import TestClient
from PIL import Image
from app.main import app,current_user
from app.core import db, validate_init_data

def signed(user_id=99,auth_date=None,token='123:TEST'):
    fields={'auth_date':str(auth_date if auth_date is not None else int(time.time())), 'query_id':'abc', 'user':json.dumps({'id':user_id,'first_name':'Тест','username':'tester'})}
    check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    key=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    fields['hash']=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(fields)

class AuthTests(unittest.TestCase):
    def test_valid(self): self.assertEqual(validate_init_data(signed(),token='123:TEST')['id'],99)
    def test_tamper(self):
        with self.assertRaises(ValueError): validate_init_data(signed().replace('abc','evil'),token='123:TEST')
    def test_expired(self):
        with self.assertRaises(ValueError): validate_init_data(signed(auth_date=100),token='123:TEST')
    def test_future(self):
        with self.assertRaises(ValueError): validate_init_data(signed(auth_date=int(time.time())+100),token='123:TEST')
    def test_duplicate(self):
        with self.assertRaises(ValueError): validate_init_data(signed()+'&query_id=abc',token='123:TEST')

class AppTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        app.dependency_overrides[current_user]=lambda:{'id':99,'first_name':'Тест','username':'tester'}
        with db() as c:
            for t in ['photos','outbox','requests','favorites','products']: c.execute('DELETE FROM '+t)
            c.execute("INSERT INTO products(id,title,price,category,image,description) VALUES (1,'Куртка',10000,'Одежда',NULL,'Тест')")
    def tearDown(self): app.dependency_overrides.clear();self.client.close()
    def payload(self,**kwargs): return {'idempotency':'request-test-00000001','description':'Нужна куртка','quantity':'1',**kwargs}
    def test_health(self): self.assertTrue(self.client.get('/health').json()['ok'])
    def test_products_search(self):
        self.assertEqual(len(self.client.get('/api/products?q=Курт').json()),1)
        self.assertEqual(len(self.client.get('/api/products?category=Обувь').json()),0)
    def test_favorites(self):
        self.assertEqual(self.client.put('/api/favorites/1',json={'favorite':True}).status_code,200)
        self.assertEqual(len(self.client.get('/api/products?favorites=true').json()),1)
        app.dependency_overrides[current_user]=lambda:{'id':100}
        self.assertEqual(self.client.get('/api/products?favorites=true').json(),[])
    def test_request_idempotent(self):
        a=self.client.post('/api/requests',data=self.payload());b=self.client.post('/api/requests',data=self.payload())
        self.assertEqual(a.status_code,201);self.assertEqual(a.json(),b.json())
        rows=self.client.get('/api/requests').json();self.assertEqual(len(rows),1);self.assertFalse(rows[0]['delivered'])
        with db() as c: self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
    def test_required_description_or_photo(self): self.assertEqual(self.client.post('/api/requests',data=self.payload(description='')).status_code,422)
    def test_invalid_link(self): self.assertEqual(self.client.post('/api/requests',data=self.payload(link='javascript:alert(1)')).status_code,422)
    def test_quantity(self): self.assertEqual(self.client.post('/api/requests',data=self.payload(quantity='0')).status_code,422)
    def test_product_only(self): self.assertEqual(self.client.post('/api/requests',data=self.payload(description='',product_id='1')).status_code,201)
    def test_photo_access(self):
        image=io.BytesIO();Image.new('RGB',(30,30),'red').save(image,'PNG')
        response=self.client.post('/api/requests',data=self.payload(description=''),files={'photos':('item.png',image.getvalue(),'image/png')})
        self.assertEqual(response.status_code,201,response.text)
        rows=self.client.get('/api/requests').json();pid=rows[0]['photos'][0]
        self.assertEqual(self.client.get('/api/photos/'+pid).status_code,200)
        app.dependency_overrides[current_user]=lambda:{'id':100}
        self.assertEqual(self.client.get('/api/photos/'+pid).status_code,404)
        self.assertEqual(self.client.get('/api/requests').json(),[])
    def test_fake_photo(self):
        r=self.client.post('/api/requests',data=self.payload(),files={'photos':('bad.jpg',b'not-an-image','image/jpeg')})
        self.assertEqual(r.status_code,422)
    def test_limit(self):
        for i in range(10): self.assertEqual(self.client.post('/api/requests',data=self.payload(idempotency=f'request-test-{i:016}')).status_code,201)
        self.assertEqual(self.client.post('/api/requests',data=self.payload(idempotency='request-test-over-limit')).status_code,429)
    def test_static(self): self.assertEqual(self.client.get('/').status_code,200)
    def test_auth_required(self):
        app.dependency_overrides.clear()
        self.assertEqual(self.client.get('/api/me',headers={'X-Telegram-Init-Data':'bad'}).status_code,401)

if __name__=='__main__': unittest.main()
