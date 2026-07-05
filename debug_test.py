from app import create_app
app = create_app()
app.config['TESTING'] = True

with app.test_client() as c:
    # Register
    c.post('/register', data={
        'name': 'Debug User', 'email': 'debugtest9999@test.com',
        'password': 'Test1234', 'business_name': 'Debug Biz',
        'whatsapp_number': '9999999999'
    }, follow_redirects=True)

    # Login
    c.post('/login', data={'email': 'debugtest9999@test.com', 'password': 'Test1234'}, follow_redirects=True)

    # Select plan
    r = c.post('/select-plan/free', follow_redirects=True)
    print('SELECT PLAN STATUS:', r.status_code)
    if r.status_code != 200:
        print(r.data.decode()[:2000])
    else:
        print('Select plan OK')

    # Dashboard
    r = c.get('/', follow_redirects=True)
    print('DASHBOARD STATUS:', r.status_code)
    if r.status_code != 200:
        print(r.data.decode()[:2000])
    else:
        txt = r.data.decode()
        print('Has menu link:', '/menu' in txt)
        print('Has orders link:', '/orders' in txt)
