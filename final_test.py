from app import create_app
app = create_app()
app.config['TESTING'] = True
ok = fail = 0

def chk(name, cond, detail=''):
    global ok, fail
    ok += cond; fail += (not cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f' | {detail}' if detail else ''))

with app.test_client() as c:
    # Setup: register + login + plan
    c.post('/register', data={'name':'Client Test','email':'clienttest@botify.com','password':'Test1234','business_name':'Test Biz','whatsapp_number':'9000000000'}, follow_redirects=True)
    c.post('/login', data={'email':'clienttest@botify.com','password':'Test1234'}, follow_redirects=True)
    r = c.post('/select-plan/free', follow_redirects=True)
    chk('Auth + Plan', r.status_code == 200)

    # All pages
    for url, name in [('/', 'Dashboard'), ('/menu', 'Menu'), ('/orders', 'Orders'),
                      ('/analytics', 'Analytics'), ('/broadcast', 'Broadcast'),
                      ('/bookings', 'Bookings'), ('/upgrade', 'Upgrade'),
                      ('/business-info', 'Business Info'), ('/add-service', 'Add Service')]:
        r = c.get(url, follow_redirects=True)
        chk(f'GET {url} ({name})', r.status_code == 200, f'status={r.status_code}')

    # Add service
    r = c.post('/add-service', data={'service_name':'Butter Chicken','category':'Main Course','price':'200','description':'Creamy'}, follow_redirects=True)
    chk('POST add-service → menu', r.status_code == 200 and b'Butter Chicken' in r.data)

    # Toggle
    import re
    svc_ids = re.findall(rb'toggle-service/(\d+)', r.data)
    if svc_ids:
        r2 = c.post(f'/toggle-service/{svc_ids[-1].decode()}')
        chk('Toggle service', r2.status_code == 200 and r2.json.get('success'))

print(f'\n=== FLASK CLIENT: {ok} PASSED | {fail} FAILED ===')
