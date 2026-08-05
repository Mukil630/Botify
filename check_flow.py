import requests, time, re

BASE = 'http://127.0.0.1:5000'
s = requests.Session()
email = f'final_{int(time.time())}@test.com'
ok, fail = 0, 0

def chk(name, cond, detail=''):
    global ok, fail
    sym = 'PASS' if cond else 'FAIL'
    ok += cond; fail += (not cond)
    print(f'  {sym}  {name}' + (f' | {detail}' if detail else ''))

print('\n=== FINAL BOTIFY VERIFICATION ===\n')

# Auth flow
s.post(BASE + '/register', data={'name':'Final User','email':email,'password':'Test1234','business_name':'Curry House','whatsapp_number':'9876543210'}, allow_redirects=True)
r = s.post(BASE + '/login', data={'email':email,'password':'Test1234'}, allow_redirects=True)
chk('Login', 'login' not in r.url.lower(), f'URL: {r.url}')
r = s.post(BASE + '/select-plan/free', allow_redirects=True)
chk('Select free plan', r.status_code == 200)

# Core pages
pages = [
    ('/', 'Dashboard', ['/menu', '/orders', '/analytics', '/broadcast', '/bookings']),
    ('/menu', 'Menu page', ['Add Item', 'Menu Management']),
    ('/add-service', 'Add Service page', ['service_name', 'category']),
    ('/orders', 'Orders page', ['status=pending', 'Orders']),
    ('/analytics', 'Analytics page', ['msgChart', 'Analytics']),
    ('/broadcast', 'Broadcast page', ['broadcastMsg', 'Broadcast']),
    ('/bookings', 'Bookings page', ['Bookings']),
    ('/business-info', 'Business Info page', ['address', 'Business']),
    ('/upgrade', 'Upgrade page', ['Upgrade', 'plan']),
    ('/feedback', 'Feedback page', ['Customer Feedback', 'Rating']),
    ('/live-chat', 'Live Chat page', ['Live Inbox', 'conversations']),
]
for url, name, keywords in pages:
    r = s.get(BASE + url, allow_redirects=True)
    found = [kw for kw in keywords if kw in r.text]
    chk(name, r.status_code == 200 and len(found) >= 1, f'Status:{r.status_code} found:{found}')

# Add menu item
r = s.post(BASE + '/add-service', data={'service_name':'Dal Makhani','category':'Food','price':'120','description':'Creamy lentil'}, allow_redirects=True)
chk('Add menu item & redirect to /menu', '/menu' in r.url and 'Dal Makhani' in r.text, f'URL:{r.url}')

# Toggle service
r = s.get(BASE + '/menu')
svc_id = re.findall(r'toggle-service/(\d+)', r.text)
if svc_id:
    r2 = s.post(BASE + f'/toggle-service/{svc_id[-1]}')
    chk('Toggle availability', r2.status_code == 200 and r2.json().get('success'))

# Save order via API (Node.js path)
# API Calls require authorization headers now
headers = {'Authorization': 'botify-super-secret-key-2025'}

# Get the user_id by checking the msg-count response
r_mc = s.get(BASE + '/api/msg-count')
chk('API /api/msg-count', r_mc.status_code == 200 and 'today_count' in r_mc.json())

# Find user id from page
r_dash = s.get(BASE + '/')
uid_m = re.search(r'/api/disconnect/(\d+)', r_dash.text)
if uid_m:
    uid = uid_m.group(1)
    print(f'  INFO  user_id = {uid}')
    
    r_menu = requests.get(BASE + f'/api/get-menu/{uid}', headers=headers)
    chk('API get-menu (Node.js)', r_menu.status_code == 200 and 'menu' in r_menu.json(), f'items:{len(r_menu.json().get("menu",[]))}')
    
    r_ord = requests.post(BASE + f'/api/save-order/{uid}', headers=headers, json={
        'customer_phone':'9900990099','customer_name':'Kumar','items':[{'name':'Dal Makhani','qty':1,'price':120}],'total_amount':120
    })
    chk('API save-order (Node.js)', r_ord.status_code == 200 and r_ord.json().get('success'))

    # Verify on orders page
    r_op = s.get(BASE + '/orders')
    chk('Order shows on orders page', 'Kumar' in r_op.text)

    r_fb = requests.post(BASE + f'/api/save-feedback/{uid}', headers=headers, json={'customer_phone':'9900990099','customer_name':'Kumar','rating':5,'comment':'Best!'})
    chk('API save-feedback (Node.js)', r_fb.status_code == 200 and r_fb.json().get('success'))

    # Analytics after real data
    r_an = s.get(BASE + '/analytics')
    chk('Analytics with data', r_an.status_code == 200 and 'msgChart' in r_an.text)

# Admin
admin = requests.Session()
r_adm = admin.post(BASE + '/admin/login', data={'password':'admin@Muki123'}, allow_redirects=True)
chk('Admin login', '/admin/dashboard' in r_adm.url)
chk('Admin users', admin.get(BASE + '/admin/users').status_code == 200)
chk('Admin revenue', admin.get(BASE + '/admin/revenue').status_code == 200)
chk('Admin support', admin.get(BASE + '/admin/support').status_code == 200)

print(f'\n=== {ok} PASSED | {fail} FAILED out of {ok+fail} ===\n')
