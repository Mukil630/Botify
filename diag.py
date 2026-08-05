import requests, json

BASE = 'http://127.0.0.1:5000'
s    = requests.Session()

s.post(BASE + '/login', data={'email': 'testbotify99@test.com', 'password': 'Test1234'})

# Test analytics
r = s.get(BASE + '/analytics')
print('ANALYTICS STATUS:', r.status_code)
if r.status_code != 200:
    print(r.text[:3000])
else:
    print('Analytics OK, has chart:', 'msgChart' in r.text)

# Test bookings
r = s.get(BASE + '/bookings')
print('BOOKINGS STATUS:', r.status_code)
if r.status_code != 200:
    print(r.text[:3000])

# Test broadcast compose field
r = s.get(BASE + '/broadcast')
print('BROADCAST STATUS:', r.status_code)
print('Has broadcastMsg:', 'broadcastMsg' in r.text)

# Test dashboard links
r = s.get(BASE + '/')
print('DASHBOARD /menu link:', '/menu' in r.text)
print('DASHBOARD /orders link:', '/orders' in r.text)
print('DASHBOARD /analytics link:', '/analytics' in r.text)

# Test add-service redirect
r = s.post(BASE + '/add-service', data={
    'service_name': 'Butter Chicken', 'category': 'Food',
    'price': '200', 'description': 'Creamy gravy'
}, allow_redirects=True)
print('ADD-SERVICE URL:', r.url)
print('ADD-SERVICE shows item:', 'Butter Chicken' in r.text)

# Test API get-menu
r = s.get(BASE + '/api/get-menu/1')
print('GET-MENU STATUS:', r.status_code)
try:
    print('GET-MENU DATA:', r.json())
except Exception as e:
    print('GET-MENU ERROR:', e, r.text[:500])
