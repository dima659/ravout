import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import test_app as support
from app import main as server
from app.core import db, DATA


def photo(size=(800,1000),mode='RGB',fmt='PNG'):
    b=io.BytesIO();Image.new(mode,size,'red').save(b,fmt);return b.getvalue()

class AdminTests(unittest.TestCase):
    setUp=support.AppTests.setUp
    tearDown=support.AppTests.tearDown
    def fields(self,**kw):
        return {'title':'Новая куртка','price':'15000','category':'Одежда','brand':'Ravout','sizes':'M, L, M','colors':'Красный, Чёрный','description':'Тестовое описание','active':'1',**kw}
    def create(self,**kw):
        with patch.object(server,'ADMIN_IDS',{99}):
            return self.client.post('/api/admin/products',data=self.fields(**kw),files={'image':('item.png',photo(),'image/png')})
    def test_non_admin_cannot_list_or_create(self):
        with patch.object(server,'ADMIN_IDS',{123}):
            self.assertEqual(self.client.get('/api/admin/products').status_code,403)
            self.assertEqual(self.client.post('/api/admin/products',data=self.fields()).status_code,403)
            self.assertEqual(self.client.put('/api/admin/products/1',data=self.fields()).status_code,403)
            self.assertEqual(self.client.put('/api/admin/products/1/visibility',json={'active':False}).status_code,403)
    def test_create_quality_photo_and_options(self):
        r=self.create();self.assertEqual(r.status_code,201,r.text);p=r.json()
        self.assertEqual(p['sizes'],['M','L']);self.assertEqual(p['colors'],['Красный','Чёрный']);self.assertTrue(p['image'].endswith('.webp'))
        with Image.open(DATA/'products'/p['image'].split('/')[-1]) as im:
            self.assertEqual(im.size,(800,1000));self.assertFalse(im.getexif())
        self.assertEqual(self.client.get(p['image']).status_code,200)
    def test_missing_photo_rejected(self):
        with patch.object(server,'ADMIN_IDS',{99}):
            self.assertEqual(self.client.post('/api/admin/products',data=self.fields()).status_code,422)
    def test_tiny_photo_rejected(self):
        with patch.object(server,'ADMIN_IDS',{99}):
            r=self.client.post('/api/admin/products',data=self.fields(),files={'image':('tiny.png',photo((100,100)),'image/png')})
            self.assertEqual(r.status_code,422);self.assertIn('600',r.json()['detail'])
    def test_fake_photo_rejected(self):
        with patch.object(server,'ADMIN_IDS',{99}):
            self.assertEqual(self.client.post('/api/admin/products',data=self.fields(),files={'image':('fake.png',b'fake','image/png')}).status_code,422)
    def test_update_keeps_existing_photo(self):
        p=self.create().json()
        with patch.object(server,'ADMIN_IDS',{99}):
            r=self.client.put('/api/admin/products/'+str(p['id']),data=self.fields(title='Другое название',price='20000'))
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['image'],p['image']);self.assertEqual(r.json()['price'],20000)
    def test_replace_photo(self):
        p=self.create().json()
        with patch.object(server,'ADMIN_IDS',{99}):
            r=self.client.put('/api/admin/products/'+str(p['id']),data=self.fields(),files={'image':('new.png',photo((1200,1400)),'image/png')})
        self.assertEqual(r.status_code,200,r.text);self.assertNotEqual(r.json()['image'],p['image'])
    def test_hide_restore_and_admin_visibility(self):
        p=self.create().json();pid=p['id']
        with patch.object(server,'ADMIN_IDS',{99}):
            self.assertEqual(self.client.put(f'/api/admin/products/{pid}/visibility',json={'active':False}).status_code,200)
            hidden=self.client.get('/api/admin/products?visibility=hidden').json();self.assertEqual(hidden['total'],1)
            self.assertEqual(len(self.client.get('/api/products?q=Новая').json()),0)
            self.assertEqual(self.client.put(f'/api/admin/products/{pid}/visibility',json={'active':True}).status_code,200)
        self.assertEqual(len(self.client.get('/api/products?q=Новая').json()),1)
    def test_combined_filters_casefold_and_price(self):
        self.create()
        r=self.client.get('/api/products?category=Одежда&brand=ravout&size=M&color=красный&min_price=14000&max_price=16000&sort=price_asc')
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(len(r.json()),1)
        self.assertEqual(self.client.get('/api/products?size=XXL').json(),[])
        self.assertEqual(self.client.get('/api/products?min_price=17000').json(),[])
        self.assertEqual(self.client.get('/api/products?min_price=200&max_price=100').status_code,422)
    def test_sort_and_pagination(self):
        self.create()
        r=self.client.get('/api/products?sort=price_asc&limit=1').json();self.assertEqual(r[0]['price'],10000)
        r=self.client.get('/api/products?sort=price_asc&limit=1&offset=1').json();self.assertEqual(r[0]['price'],15000)
        self.assertEqual(self.client.get('/api/products?sort=DROP').status_code,422)
    def test_metadata_and_me(self):
        self.create()
        f=self.client.get('/api/catalog-filters').json();self.assertIn('Ravout',f['brands']);self.assertIn('M',f['sizes']);self.assertIn('Красный',f['colors'])
        with patch.object(server,'ADMIN_IDS',{99}): self.assertTrue(self.client.get('/api/me').json()['is_admin'])
    def test_invalid_form(self):
        self.assertEqual(self.create(price='-1').status_code,422)
        self.assertEqual(self.create(category='Invalid').status_code,422)
        self.assertEqual(self.create(title='').status_code,422)
        self.assertEqual(self.create(sizes=','.join(str(x) for x in range(26))).status_code,422)
    def test_missing_product(self):
        with patch.object(server,'ADMIN_IDS',{99}):
            self.assertEqual(self.client.put('/api/admin/products/1000000',data=self.fields()).status_code,404)
    def test_title_is_data_not_html(self):
        r=self.create(title='<script>alert(1)</script>');self.assertEqual(r.status_code,201);self.assertEqual(r.json()['title'],'<script>alert(1)</script>')

class MigrationTests(unittest.TestCase):
    def test_old_catalog_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            c=sqlite3.connect(directory+'/shop.sqlite3')
            c.execute("CREATE TABLE products (id INTEGER PRIMARY KEY,title TEXT NOT NULL,price INTEGER NOT NULL,category TEXT NOT NULL,image TEXT,description TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1)")
            c.execute("INSERT INTO products(id,title,price,category) VALUES(7,'Старый товар',9000,'Одежда')");c.commit();c.close()
            env={**os.environ,'DATA_DIR':directory,'DEV_MODE':'true'}
            code="from app.core import db;\nwith db() as c:\n r=c.execute('SELECT * FROM products WHERE id=7').fetchone();assert r['title']=='Старый товар';assert r['sizes']=='[]';assert r['brand']=='';print('ok')"
            result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
