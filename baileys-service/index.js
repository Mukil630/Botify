require('dotenv').config()

const { default: makeWASocket, DisconnectReason, useMultiFileAuthState, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys')
const express = require('express')
const qrcode  = require('qrcode')
const fs      = require('fs')
const path    = require('path')
const P       = require('pino')
const Groq    = require('groq-sdk')
const axios   = require('axios')

const app = express()
app.use(express.json())

axios.defaults.headers.common['Authorization'] = process.env.SECRET_KEY || 'botify-super-secret-key-2025'

const groq     = new Groq({ apiKey: process.env.GROQ_API_KEY })
const FLASK_URL = process.env.FLASK_URL || 'http://127.0.0.1:5000'

const connections    = {}
const qrCodes        = {}
const botConfigs     = {}
const bookingState   = {}
const orderState     = {}
const feedbackState  = {}
const messageQueues  = {}
const reconnectAttempts = {}
const pauseState     = {}
const welcomedSessions = {}
const conversationHistory = {}

// ─────────────────────────────────────────────
// UTILS
// ─────────────────────────────────────────────
function randomDelay(min = 3000, max = 7000) {
    return new Promise(r => setTimeout(r, Math.floor(Math.random() * (max - min + 1)) + min))
}

async function sendPdfBill(userId, sock, from, orderId, customerName, customerPhone) {
    try {
        const billRes = await axios.get(`${FLASK_URL}/api/generate-bill/${orderId}`, {
            responseType: 'arraybuffer',
            timeout: 12000
        })

        await sock.sendMessage(from, {
            document: Buffer.from(billRes.data),
            mimetype: 'application/pdf',
            fileName: `Order_${orderId}_Receipt.pdf`,
            caption: `📄 Hi ${customerName}! Here is your dynamic PDF receipt/invoice for Order #${orderId}. Thank you! 🙏`
        })
        await logMessage(userId, from, customerName, `[PDF Receipt Sent for Order #${orderId}]`, 'bot')
    } catch (err) {
        console.error(`❌ Failed to send PDF receipt:`, err.message)
    }
}

async function sendWithTyping(sock, jid, text) {
    try {
        await sock.sendPresenceUpdate('composing', jid)
        await new Promise(r => setTimeout(r, Math.min(text.length * 50, 4000)))
        await sock.sendPresenceUpdate('paused', jid)
        await randomDelay(1500, 4000)
        await sock.sendMessage(jid, { text })
    } catch (err) {
        try { await sock.sendMessage(jid, { text }) } catch (e) {}
    }
}

async function queueMessage(userId, fn) {
    if (!messageQueues[userId]) messageQueues[userId] = Promise.resolve()
    messageQueues[userId] = messageQueues[userId].then(fn).catch(err => {
        console.error(`❌ Queue error for ${userId}:`, err.message)
    })
    return messageQueues[userId]
}

function getReconnectDelay(userId) {
    const attempts = reconnectAttempts[userId] || 0
    const delay    = Math.min(3000 * Math.pow(2, attempts), 60000)
    reconnectAttempts[userId] = attempts + 1
    return delay
}

function isPhoneNumber(text) { return /^\d{10}$/.test(text.trim()) }

function isDatePattern(text) {
    return [/\d{1,2}-\d{1,2}-\d{4}/, /\d{1,2}\/\d{1,2}\/\d{4}/, /\d{1,2}[:.]\d{2}\s*(am|pm)?/i,
            /today|tomorrow|morning|afternoon|evening|night|\d+(st|nd|rd|th)|january|february|march|april|may|june|july|august|september|october|november|december/i
    ].some(p => p.test(text))
}

// ─────────────────────────────────────────────
// FLASK API HELPERS
// ─────────────────────────────────────────────
async function checkAndIncrementLimit(userId) {
    try {
        const res = await axios.post(`${FLASK_URL}/api/check-limit/${userId}`, {}, { timeout: 3000 })
        return res.data
    } catch (err) {
        return { allowed: true, count: 0, limit: 999 }
    }
}

async function logMessage(userId, customerPhone, customerName, messageText, sender) {
    try {
        await axios.post(`${FLASK_URL}/api/log-message/${userId}`,
            { customer_phone: customerPhone, customer_name: customerName, message_text: messageText, sender },
            { timeout: 5000 })
    } catch (err) {}
}

async function saveBooking(userId, bookingData) {
    try {
        const res = await axios.post(`${FLASK_URL}/api/save-booking/${userId}`, bookingData, { timeout: 5000 })
        return res.data
    } catch (err) { return null }
}

async function saveOrder(userId, orderData) {
    try {
        const res = await axios.post(`${FLASK_URL}/api/save-order/${userId}`, orderData, { timeout: 5000 })
        return res.data
    } catch (err) { return null }
}

async function saveFeedback(userId, feedbackData) {
    try {
        await axios.post(`${FLASK_URL}/api/save-feedback/${userId}`, feedbackData, { timeout: 5000 })
    } catch (err) {}
}

async function getMenu(userId) {
    try {
        const res = await axios.get(`${FLASK_URL}/api/get-menu/${userId}`, { timeout: 5000 })
        return res.data.menu || []
    } catch (err) { return [] }
}

async function checkBookingSlot(userId, service, dateTime) {
    try {
        const res = await axios.get(`${FLASK_URL}/api/check-booking-slot/${userId}`,
            { params: { service, date_time: dateTime }, timeout: 5000 })
        return res.data
    } catch (err) { return { available: true } }
}

// ─────────────────────────────────────────────
// FORMAT MENU TEXT
// ─────────────────────────────────────────────
function formatMenuText(menuItems, businessName) {
    if (!menuItems || menuItems.length === 0) return '❌ No menu items available yet.'

    const grouped = {}
    menuItems.forEach(item => {
        const cat = item.category || 'Menu'
        if (!grouped[cat]) grouped[cat] = []
        grouped[cat].push(item)
    })

    let text = `🍽️ *${businessName || 'Our'} Menu*\n\n`
    for (const [cat, items] of Object.entries(grouped)) {
        text += `*— ${cat} —*\n`
        items.forEach(item => {
            text += `• *${item.name}*`
            if (item.price) text += ` — ₹${item.price}`
            if (item.description) text += `\n  _${item.description}_`
            text += '\n'
        })
        text += '\n'
    }
    text += `💬 Reply *ORDER* to place an order\n📅 Reply *BOOK* to make a booking`
    return text
}

// ─────────────────────────────────────────────
// AI: EXTRACT SERVICE
// ─────────────────────────────────────────────
async function extractServiceWithAI(userMessage, botConfig) {
    try {
        const completion = await groq.chat.completions.create({
            messages: [{ role: 'user', content: `Available services:\n${botConfig.services}\n\nUser said: "${userMessage}"\n\nIf user mentioned ANY service from the list, return ONLY the exact service name. Otherwise return: NOT_FOUND` }],
            model: 'llama-3.3-70b-versatile', max_tokens: 50
        })
        const result = completion.choices[0]?.message?.content?.trim() || 'NOT_FOUND'
        return result !== 'NOT_FOUND' ? result : null
    } catch (err) { return null }
}

// ─────────────────────────────────────────────
// AI: GENERAL REPLY
// ─────────────────────────────────────────────
async function getAIReply(userMessage, botConfig, from, currentState = {}) {
    try {
        if (!conversationHistory[from]) conversationHistory[from] = []
        conversationHistory[from].push({ role: 'user', content: userMessage })
        if (conversationHistory[from].length > 20) conversationHistory[from].shift()

        const historyText = conversationHistory[from].slice(-8).map(entry => {
            const label = entry.role === 'user' ? 'Customer' : 'Bot'
            return `${label}: ${entry.content}`
        }).join('\n')

        const systemPrompt = `You are a friendly and professional WhatsApp assistant for ${botConfig.business_name || 'this business'}.

BUSINESS DETAILS:
- Business Name: ${botConfig.business_name || ''}
- Welcome Message: ${botConfig.welcome_message || ''}
- Address: ${botConfig.address || 'Not provided'}
- Timings: ${botConfig.timings || 'Not provided'}
- Contact Phone: ${botConfig.contact_phone || 'Not provided'}
- Contact Email: ${botConfig.contact_email || 'Not provided'}
- Website: ${botConfig.website || 'Not provided'}
- Extra Info: ${botConfig.extra_info || ''}
${botConfig.personality ? `\nCUSTOM PERSONALITY:\n${botConfig.personality}` : ''}

SERVICES/PRODUCTS:
${botConfig.services || 'No services listed yet'}

CURRENT CUSTOMER STATE:
- Ordering: ${currentState.is_ordering || false}
- Order step: ${currentState.step || 'idle'}
- Order items: ${JSON.stringify(currentState.items || [])}
- Booking: ${currentState.isBooking || false}

RECENT CONVERSATION:
${historyText || 'No recent conversation.'}

RULES:
1. If the customer is already ordering, help complete the order and do NOT say "type ORDER".
2. If the customer is already booking, help complete the booking.
3. If the customer asks for bill or order details after ordering, respond with status and next steps.
4. Reply in the same language as the customer.
5. Never say you are an AI.
6. Keep replies short, warm, and helpful.
7. If the customer asks for MENU, show menu or ask them to type MENU if no items are available.
8. If the customer mentions their items directly, treat it as an order intent.`

        const completion = await groq.chat.completions.create({
            messages: [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: userMessage }
            ],
            model: 'llama-3.3-70b-versatile',
            max_tokens: 400
        })

        const reply = completion.choices[0]?.message?.content || '👋 Hello! How can I help you?'
        conversationHistory[from].push({ role: 'assistant', content: reply })
        if (conversationHistory[from].length > 20) conversationHistory[from].shift()
        return reply
    } catch (err) {
        return '👋 Hello! How can I help you today?'
    }
}

// ─────────────────────────────────────────────
// AUTO-RESTORE
// ─────────────────────────────────────────────
async function restoreActiveBots() {
    try {
        const response = await axios.get(`${FLASK_URL}/api/active-bots`, { timeout: 5000 })
        const bots = response.data.bots || []
        console.log(`Found ${bots.length} active bot(s) — restoring...`)
        for (const bot of bots) {
            await startConnection(bot.user_id, bot)
            await new Promise(r => setTimeout(r, 1000))
        }
    } catch (err) {
        console.log('⚠️ Could not restore bots:', err.message)
    }
}

// ─────────────────────────────────────────────
// MAIN: START CONNECTION
// ─────────────────────────────────────────────
async function startConnection(userId, botConfig = {}) {
    console.log(`\n🚀 Starting connection for user ${userId}`)
    botConfigs[userId] = botConfig

    if (connections[userId]?.status === 'connected') {
        connections[userId].botConfig = botConfig
        console.log(`✅ User ${userId} already connected — config updated!`)
        return
    }

    const authFolder = path.join(__dirname, 'auth', userId.toString())
    if (!fs.existsSync(authFolder)) fs.mkdirSync(authFolder, { recursive: true })

    const { state, saveCreds } = await useMultiFileAuthState(authFolder)
    const { version }          = await fetchLatestBaileysVersion()

    const sock = makeWASocket({
        version, auth: state, printQRInTerminal: false, logger: P({ level: 'silent' })
    })

    sock.ev.on('creds.update', saveCreds)

    // ── CONNECTION UPDATES ──
    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update

        if (qr) {
            qrCodes[userId]     = await qrcode.toDataURL(qr)
            connections[userId] = { status: 'qr_ready', sock, botConfig }
        }

        if (connection === 'open') {
            reconnectAttempts[userId] = 0
            connections[userId] = { status: 'connected', sock, botConfig }
            qrCodes[userId]     = null
            console.log(`✅ User ${userId} connected!`)
            try {
                const ownerNumber = botConfig.whatsapp_number || ''
                if (ownerNumber) {
                    const jid = ownerNumber.replace(/\D/g, '') + '@s.whatsapp.net'
                    await sendWithTyping(sock, jid,
                        `✅ Your WhatsApp Bot is now ACTIVE!\n\n🤖 Bot: ${botConfig.bot_name || 'Your Bot'}\n🏢 Business: ${botConfig.business_name || ''}\n\nCustomers can now message you! 🚀`)
                }
            } catch (err) {}
        }

        if (connection === 'close') {
            const code = lastDisconnect?.error?.output?.statusCode
            if (code !== DisconnectReason.loggedOut) {
                connections[userId] = { status: 'reconnecting' }
                setTimeout(() => startConnection(userId, botConfigs[userId] || botConfig), getReconnectDelay(userId))
            } else {
                connections[userId] = { status: 'disconnected' }
                reconnectAttempts[userId] = 0
            }
        }
    })

    // ── INCOMING MESSAGES ──
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return
        const msg = messages[0]
        if (!msg?.message || msg.key.fromMe) return

        const from = msg.key.remoteJid
        if (from.endsWith('@g.us') || from === 'status@broadcast' || from.endsWith('@broadcast')) return

        const text = (
            msg.message?.conversation ||
            msg.message?.extendedTextMessage?.text ||
            msg.message?.imageMessage?.caption || ''
        ).trim()

        if (!text) return
        console.log(`\n📨 [User ${userId}] From ${from}: "${text}"`)

                await queueMessage(userId, async () => {
            try {
                await sock.readMessages([msg.key])

                const limitData = await checkAndIncrementLimit(userId)
                if (!limitData.allowed) {
                    await sendWithTyping(sock, from, `⚠️ Sorry, this bot has reached its daily message limit.\n\nPlease try again tomorrow or contact the business owner to upgrade their plan.`)
                    return
                }

                const config    = botConfigs[userId] || {}
                const textLower = text.toLowerCase()

                // Init states
                if (!bookingState[from]) bookingState[from] = { user_id: userId, customer_name: null, customer_phone: null, service: null, date_time: null, is_booking: false }
                if (!orderState[from])   orderState[from]   = { user_id: userId, customer_name: null, customer_phone: null, items: [], is_ordering: false, step: 'idle' }

                const booking = bookingState[from]
                const order   = orderState[from]

                await logMessage(userId, from, booking.customer_name || order.customer_name, text, 'customer')

                if (pauseState[from]) {
                    console.log(`⏸️ Bot is paused for ${from}. Ignoring message.`)
                    return
                }

                await randomDelay(800, 2000)

                // ── WELCOME GREETING WITH IMAGE ──
                const greetingKeywords = ['hi', 'hello', 'hey', 'start', 'vanakkam', 'get started', 'வணக்கம்', 'hii', 'hiii']
                const isGreeting = greetingKeywords.some(kw => textLower === kw || textLower.startsWith(kw))
                
                if ((isGreeting || !welcomedSessions[from]) && !order.is_ordering && !booking.is_booking) {
                    welcomedSessions[from] = true
                    if (config.welcome_image) {
                        try {
                            const imgUrl = config.welcome_image.startsWith('http') ? config.welcome_image : `${FLASK_URL}${config.welcome_image}`
                            await sock.sendMessage(from, {
                                image: { url: imgUrl },
                                caption: config.welcome_message || `Welcome to *${config.business_name || 'our business'}*! How can we help you today? \n\nType *MENU* to see our beautiful Catalog! 🛍️`
                            })
                            await logMessage(userId, from, booking.customer_name, '[Welcome Image Sent]', 'bot')
                        } catch (e) {
                            console.error("Failed to send welcome image:", e.message)
                            const fallbackWelcome = config.welcome_message || `Welcome to *${config.business_name || 'our business'}*! 👋\n\nQuick options: *MENU, ORDER, BOOK, INFO* 📅`
                            await sendWithTyping(sock, from, fallbackWelcome)
                            await logMessage(userId, from, booking.customer_name, fallbackWelcome, 'bot')
                        }
                    } else {
                        const fallbackWelcome = config.welcome_message || `Welcome to *${config.business_name || 'our business'}*! 👋\n\nQuick options: *MENU, ORDER, BOOK, INFO* 📅`
                        await sendWithTyping(sock, from, fallbackWelcome)
                        await logMessage(userId, from, booking.customer_name, fallbackWelcome, 'bot')
                    }
                    return
                }

                // ── FEEDBACK COLLECTION ──
                if (feedbackState[from] && feedbackState[from].awaiting_rating) {
                    const rating = parseInt(text.trim())
                    if (rating >= 1 && rating <= 5) {
                        await saveFeedback(userId, {
                            customer_phone: from,
                            customer_name: feedbackState[from].customer_name,
                            rating, comment: ''
                        })
                        const reply = `⭐ Thank you for your ${rating}/5 rating! Your feedback means a lot to us. 🙏`
                        await sendWithTyping(sock, from, reply)
                        await logMessage(userId, from, feedbackState[from].customer_name, reply, 'bot')
                        delete feedbackState[from]
                        return
                    }
                    // Optional: ask for comment
                    if (feedbackState[from].awaiting_comment) {
                        await saveFeedback(userId, {
                            customer_phone: from,
                            customer_name: feedbackState[from].customer_name,
                            rating: feedbackState[from].rating,
                            comment: text
                        })
                        const reply = `✅ Thank you! We'll keep improving. 😊`
                        await sendWithTyping(sock, from, reply)
                        await logMessage(userId, from, feedbackState[from].customer_name, reply, 'bot')
                        delete feedbackState[from]
                        return
                    }
                }

                // ── MENU REQUEST ──
                const menuKeywords = ['menu', 'price', 'list', 'items', 'services', 'what do you have', 'what do you sell', 'food', 'products']
                if (menuKeywords.some(kw => textLower.includes(kw)) && !order.is_ordering && !booking.is_booking) {
                    const menuItems  = await getMenu(userId)
                    const menuText   = formatMenuText(menuItems, config.business_name)

                    if (config.menu_image) {
                        try {
                            const imgUrl = config.menu_image.startsWith('http') ? config.menu_image : `${FLASK_URL}${config.menu_image}`
                            await sock.sendMessage(from, {
                                image: { url: imgUrl },
                                caption: `📦 *${config.business_name || 'Our'} Catalog*\n\nTap the image to view all products & services!\n\n💬 Reply *ORDER* to place an order\n📅 Reply *BOOK* to make a booking`
                            })
                            await logMessage(userId, from, booking.customer_name, '[Catalog Image Sent]', 'bot')
                        } catch (imgErr) {
                            await sendWithTyping(sock, from, menuText)
                            await logMessage(userId, from, booking.customer_name, menuText, 'bot')
                        }
                    } else {
                        await sendWithTyping(sock, from, menuText)
                        await logMessage(userId, from, booking.customer_name, menuText, 'bot')
                    }
                    return
                }

                // ── ORDER FLOW ──
                const orderKeywords = ['order', 'buy', 'i want', 'add to cart', 'purchase', 'place order', 'want', 'need', 'venum', 'vennum', 'enaku', 'எனக்கு', 'வேணும்', 'வேண்டும்', 'tharungal', 'தாருங்கள்', 'order pannanum', 'ஒர்டர்']
                let isOrderIntent = orderKeywords.some(kw => textLower.includes(kw))

                if (!isOrderIntent && !order.is_ordering && !booking.is_booking) {
                    const menuItems = await getMenu(userId)
                    const normalizedText = textLower.replace(/[^a-z0-9அஆஇஈஉஊஎஏஐஒஓஔகுஙசஞடணதநபமயரலவழளறன்ழ்ஷஸ்ஹ\s]/gi, ' ')
                    const mentionsMenuItem = menuItems.some(item => item.name && normalizedText.includes(item.name.toLowerCase()))
                    if (mentionsMenuItem) isOrderIntent = true
                }

                if (isOrderIntent && !order.is_ordering && !booking.is_booking) {
                    order.is_ordering = true
                    order.step        = 'menu'
                    const menuItems   = await getMenu(userId)
                    const menuText    = formatMenuText(menuItems, config.business_name)
                    const reply       = `🛒 *Place Your Order*\n\n${menuText}\n\n📝 *Type the item(s) you want to order:*\nExample: "2 Chicken Biryani, 1 Coke"`
                    await sendWithTyping(sock, from, reply)
                    await logMessage(userId, from, order.customer_name, reply, 'bot')
                    return
                }

                if (order.is_ordering) {
                    // Collect items
                    if (order.step === 'menu' && text.length > 2) {
                        // Parse items with AI
                        const menuItems = await getMenu(userId)
                        const menuText  = menuItems.map(m => `${m.name} - ${m.price}`).join(', ')
                        let parsedItems = []
                        try {
                            const completion = await groq.chat.completions.create({
                                messages: [{ role: 'user', content: `Menu: ${menuText}\n\nCustomer ordered: "${text}"\n\nParse and return JSON array: [{"name":"item name","qty":1,"price":number}]\nUse exact names from menu. Return ONLY valid JSON array, nothing else.` }],
                                model: 'llama-3.3-70b-versatile', max_tokens: 200
                            })
                            const raw = completion.choices[0]?.message?.content?.trim() || '[]'
                            parsedItems = JSON.parse(raw.match(/\[[\s\S]*\]/)?.[0] || '[]')
                        } catch (e) { parsedItems = [] }

                        if (parsedItems.length === 0) {
                            const reply = `❓ I couldn't find those items in our menu. Please type item names exactly as they appear in the menu.`
                            await sendWithTyping(sock, from, reply)
                            await logMessage(userId, from, order.customer_name, reply, 'bot')
                            return
                        }

                        order.items    = parsedItems
                        order.step     = 'name'
                        const total    = parsedItems.reduce((sum, i) => sum + (i.price * i.qty), 0)
                        const itemList = parsedItems.map(i => `• ${i.name} x${i.qty} — ₹${i.price * i.qty}`).join('\n')
                        const reply    = `🛒 *Order Summary:*\n${itemList}\n\n💰 *Total: ₹${total}*\n\n👤 Please share your *Name*:`
                        await sendWithTyping(sock, from, reply)
                        await logMessage(userId, from, order.customer_name, reply, 'bot')
                        return
                    }

                    if (order.step === 'name' && !isPhoneNumber(text) && text.length > 1) {
                        order.customer_name = text.trim()
                        order.step          = 'phone'
                        const reply         = `📱 Please share your *Phone Number* (10 digits):`
                        await sendWithTyping(sock, from, reply)
                        await logMessage(userId, from, order.customer_name, reply, 'bot')
                        return
                    }

                    if (order.step === 'phone' && isPhoneNumber(text)) {
                        order.customer_phone = text.trim()
                        const total          = order.items.reduce((sum, i) => sum + (i.price * i.qty), 0)
                        const itemList       = order.items.map(i => `• ${i.name} x${i.qty} — ₹${i.price * i.qty}`).join('\n')

                        // Save order to Flask
                        const orderRes = await saveOrder(userId, {
                            customer_name:  order.customer_name,
                            customer_phone: order.customer_phone,
                            items:          order.items,
                            total_amount:   total
                        })

                        const confirm = `✅ *ORDER CONFIRMED!*\n\n${itemList}\n\n💰 *Total: ₹${total}*\n👤 Name: ${order.customer_name}\n📱 Phone: ${order.customer_phone}\n\n⏳ We'll prepare your order shortly!\n🚀 You'll receive a status update soon.`
                        await sendWithTyping(sock, from, confirm)
                        await logMessage(userId, from, order.customer_name, confirm, 'bot')

                        // Send PDF bill receipt if successfully saved
                        if (orderRes && orderRes.success && orderRes.order_id) {
                            await sendPdfBill(userId, sock, from, orderRes.order_id, order.customer_name, order.customer_phone)
                        }

                        // Send Thanks Image if configured
                        if (config.thanks_image) {
                            try {
                                const imgUrl = config.thanks_image.startsWith('http') ? config.thanks_image : `${FLASK_URL}${config.thanks_image}`
                                await sock.sendMessage(from, {
                                    image: { url: imgUrl },
                                    caption: `Thank you for choosing ${config.business_name || 'us'}! 🙏`
                                })
                                await logMessage(userId, from, order.customer_name, '[Thanks Image Sent]', 'bot')
                            } catch (e) { console.error('Failed to send thanks image', e) }
                        }

                        // Send Payment UPI QR if configured
                        if (config.upi_id) {
                            try {
                                const bName = encodeURIComponent(config.business_name || 'Business')
                                const upiUrl = `upi://pay?pa=${config.upi_id}&pn=${bName}&am=${total}&cu=INR&tn=Order_from_${order.customer_phone}`
                                const qrBuffer = await qrcode.toBuffer(upiUrl)
                                await sock.sendMessage(from, {
                                    image: qrBuffer,
                                    caption: `💳 *Scan & Pay via UPI*\n(GPay, PhonePe, Paytm, etc)\n\nAmount: ₹${total}\nUPI ID: ${config.upi_id}`
                                })
                                await logMessage(userId, from, order.customer_name, '[UPI QR Sent]', 'bot')
                            } catch (e) { console.error('Failed to send UPI QR', e) }
                        }

                        // Notify owner
                        try {
                            const ownerNum = config.whatsapp_number || ''
                            if (ownerNum) {
                                const ownerJid = ownerNum.replace(/\D/g, '') + '@s.whatsapp.net'
                                await sendWithTyping(sock, ownerJid, `🔔 *NEW ORDER!*\n\n${itemList}\n\n💰 Total: ₹${total}\n👤 ${order.customer_name}\n📱 ${order.customer_phone}\n\n👉 Update status on dashboard!`)
                            }
                        } catch (e) {}

                        // Reset order state
                        delete orderState[from]

                        // Schedule feedback request after 2 hours
                        setTimeout(async () => {
                            if (connections[userId]?.status === 'connected') {
                                feedbackState[from] = { awaiting_rating: true, customer_name: order.customer_name }
                                const fbReq = `😊 Hi ${order.customer_name}! How was your order from *${config.business_name || 'us'}*?\n\nPlease rate us from *1 to 5* ⭐`
                                try {
                                    const s = connections[userId]?.sock
                                    if (s) await sendWithTyping(s, from, fbReq)
                                } catch (e) {}
                            }
                        }, 2 * 60 * 60 * 1000) // 2 hours

                        return
                    }

                    if (order.step === 'phone' && !isPhoneNumber(text)) {
                        const reply = `❌ Please enter a valid 10-digit phone number.`
                        await sendWithTyping(sock, from, reply)
                        await logMessage(userId, from, order.customer_name, reply, 'bot')
                        return
                    }
                }

                // ── BOOKING FLOW ──
                const bookingKeywords = ['book', 'booking', 'appointment', 'reserve', 'table']
                const isBookingIntent = bookingKeywords.some(kw => textLower.includes(kw))

                if (isBookingIntent && !booking.is_booking && !order.is_ordering) {
                    booking.is_booking = true
                    const reply = `📅 Great! I'll help you book.\n\nPlease share:\n1️⃣ Your Name\n2️⃣ Service you want\n3️⃣ Preferred date & time\n4️⃣ Your phone number (10 digits)\n\nWe will confirm shortly! ✅`
                    await sendWithTyping(sock, from, reply)
                    await logMessage(userId, from, booking.customer_name, reply, 'bot')
                    return
                }

                if (booking.is_booking) {
                    if (isPhoneNumber(text) && !booking.customer_phone) booking.customer_phone = text.trim()
                    if (isDatePattern(text) && !booking.date_time) booking.date_time = text.trim()
                    if (!booking.service) {
                        const extracted = await extractServiceWithAI(text, config)
                        if (extracted) booking.service = extracted
                    }
                    if (!booking.customer_name && !isPhoneNumber(text) && !isDatePattern(text) && text.length < 50 &&
                        !text.includes(' at ') && !text.includes(' on ') && !text.includes('booking')) {
                        booking.customer_name = text.trim()
                    }

                    if (booking.customer_name && booking.customer_phone && booking.service && booking.date_time) {
                        const slotCheck = await checkBookingSlot(userId, booking.service, booking.date_time)
                        if (!slotCheck.available) {
                            const slots = slotCheck.available_slots?.join(', ') || 'Please try a different time'
                            const slotMsg = `❌ Sorry! This slot (${booking.date_time}) is already booked.\n\n⏰ Available times:\n${slots}\n\nPlease choose another time! 😊`
                            await sendWithTyping(sock, from, slotMsg)
                            await logMessage(userId, from, booking.customer_name, slotMsg, 'bot')
                            booking.date_time = null
                            return
                        }

                        await saveBooking(userId, { customer_name: booking.customer_name, customer_phone: booking.customer_phone, service: booking.service, date_time: booking.date_time })
                        const confirm = `✅ BOOKING CONFIRMED!\n\n👤 Name: ${booking.customer_name}\n🛎️ Service: ${booking.service}\n📅 Date/Time: ${booking.date_time}\n📱 Phone: ${booking.customer_phone}\n\nWe will contact you soon! Thank you! 🙏`
                        await sendWithTyping(sock, from, confirm)
                        await logMessage(userId, from, booking.customer_name, confirm, 'bot')

                        // Schedule feedback after 24 hours
                        const customerName = booking.customer_name
                        const customerPhone = from
                        setTimeout(async () => {
                            if (connections[userId]?.status === 'connected') {
                                feedbackState[customerPhone] = { awaiting_rating: true, customer_name: customerName }
                                const fbReq = `😊 Hi ${customerName}! We hope your experience with *${config.business_name || 'us'}* was great!\n\nPlease rate your experience from *1 to 5* ⭐`
                                try {
                                    const s = connections[userId]?.sock
                                    if (s) await sendWithTyping(s, customerPhone, fbReq)
                                } catch (e) {}
                            }
                        }, 24 * 60 * 60 * 1000) // 24 hours

                        delete bookingState[from]
                        return
                    }

                    const missing = []
                    if (!booking.customer_name)  missing.push('1️⃣ Name')
                    if (!booking.service)        missing.push('2️⃣ Service')
                    if (!booking.date_time)      missing.push('3️⃣ Date & Time')
                    if (!booking.customer_phone) missing.push('4️⃣ Phone Number')
                    const progress = `Got it! Still need:\n${missing.join('\n')}\n\nPlease share the missing details.`
                    await sendWithTyping(sock, from, progress)
                    await logMessage(userId, from, booking.customer_name, progress, 'bot')
                    return
                }

                // ── ORDER HISTORY REQUEST ──
                const orderHistoryKeywords = ['my orders', 'order history', 'past orders', 'my order', 'order status', 'where is my order']
                if (orderHistoryKeywords.some(kw => textLower.includes(kw))) {
                    try {
                        const FLASK_URL = process.env.FLASK_URL || 'http://127.0.0.1:5000'
                        const phoneClean = from.replace('@s.whatsapp.net', '')
                        const ordersRes = await axios.get(`${FLASK_URL}/api/customer-orders/${userId}/${phoneClean}`, { timeout: 5000 })
                        const orders = ordersRes.data.orders || []

                        if (orders.length === 0) {
                            const reply = `📋 You haven't placed any orders yet!\n\nReply *ORDER* to place your first order.`
                            await sendWithTyping(sock, from, reply)
                            await logMessage(userId, from, booking.customer_name || order.customer_name, reply, 'bot')
                        } else {
                            let reply = `📋 *Your Order History*\n\n`
                            orders.forEach((o, idx) => {
                                const items = o.items.join(', ') || 'Items'
                                const statusEmoji = o.status === 'delivered' ? '✅' : o.status === 'ready' ? '🎉' : o.status === 'preparing' ? '👨‍🍳' : '⏳'
                                reply += `${idx + 1}. *Order #${o.order_id}*\n   📅 ${o.date}\n   📦 ${items}\n   💰 ₹${o.total}\n   ${statusEmoji} ${o.status.toUpperCase()}\n\n`
                            })
                            reply += `Reply with your *Order Number* to get full details.`
                            await sendWithTyping(sock, from, reply)
                            await logMessage(userId, from, booking.customer_name || order.customer_name, '[Order History Sent]', 'bot')
                        }
                    } catch (err) {
                        const reply = `📋 I couldn't fetch your orders right now. Please try again later or contact us directly!`
                        await sendWithTyping(sock, from, reply)
                    }
                    return
                }

                // ── NORMAL AI REPLY ──
                const currentState = {
                    is_ordering: order.is_ordering || false,
                    step: order.step || 'idle',
                    items: order.items || [],
                    isBooking: booking.is_booking || false
                }
                const reply = await getAIReply(text, config, from, currentState)
                await sendWithTyping(sock, from, reply)
                await logMessage(userId, from, booking.customer_name || order.customer_name, reply, 'bot')

            } catch (err) {
                console.error('❌ Message handler error:', err.message)
            }
        })
    })

    connections[userId] = { status: 'starting', sock, botConfig }
}

// ─────────────────────────────────────────────
// ROUTES
// ─────────────────────────────────────────────
app.post('/start/:userId', async (req, res) => {
    try {
        await startConnection(req.params.userId, req.body)
        res.json({ success: true, message: 'Connection started' })
    } catch (err) {
        res.json({ success: false, error: err.message })
    }
})

app.get('/qr/:userId', (req, res) => {
    const userId = req.params.userId
    res.json({ qr: qrCodes[userId] || null, status: connections[userId]?.status || 'not_started' })
})

app.get('/status/:userId', (req, res) => {
    res.json({ status: connections[req.params.userId]?.status || 'not_started' })
})

app.get('/disconnect/:userId', async (req, res) => {
    const userId = req.params.userId
    try {
        const conn = connections[userId]
        if (conn?.sock) {
            try { await conn.sock.logout() } catch (e) {}
            try { conn.sock.end()          } catch (e) {}
        }
        connections[userId]  = { status: 'disconnected' }
        qrCodes[userId]      = null
        reconnectAttempts[userId] = 0
        delete botConfigs[userId]

        const authFolder = path.join(__dirname, 'auth', userId.toString())
        if (fs.existsSync(authFolder)) fs.rmSync(authFolder, { recursive: true, force: true })

        res.json({ success: true })
    } catch (err) {
        res.json({ success: false, error: err.message })
    }
})

// ─────────────────────────────────────────────
// BROADCAST ENDPOINT
// ─────────────────────────────────────────────
app.post('/broadcast/:userId', async (req, res) => {
    const userId  = req.params.userId
    const { message, phones } = req.body

    if (!message || !phones?.length) {
        return res.json({ success: false, error: 'Message or phones missing' })
    }

    const conn = connections[userId]
    if (!conn || conn.status !== 'connected') {
        return res.json({ success: false, error: 'Bot not connected' })
    }

    res.json({ success: true, total: phones.length })

    // Send in background with rate limiting (1 per 3s)
    ;(async () => {
        let sent = 0
        for (const phone of phones) {
            try {
                const jid = phone.replace(/\D/g, '') + '@s.whatsapp.net'
                await conn.sock.sendMessage(jid, { text: message })
                sent++
                console.log(`📢 Broadcast sent to ${phone} (${sent}/${phones.length})`)
                await new Promise(r => setTimeout(r, 3000)) // 3s delay
            } catch (err) {
                console.error(`❌ Broadcast failed to ${phone}:`, err.message)
            }
        }
        console.log(`✅ Broadcast complete: ${sent}/${phones.length} sent`)
    })()
})

// ─────────────────────────────────────────────
// SEND REMINDER ENDPOINT (called by Flask scheduler)
// ─────────────────────────────────────────────
app.post('/send-reminder/:userId', async (req, res) => {
    const userId = req.params.userId
    const { customer_phone, customer_name, service, date_time } = req.body

    const conn = connections[userId]
    if (!conn || conn.status !== 'connected') {
        return res.json({ success: false, error: 'Bot not connected' })
    }

    try {
        const jid     = customer_phone.replace(/\D/g, '') + '@s.whatsapp.net'
        const message = `⏰ *Appointment Reminder!*\n\nHi ${customer_name}! This is a reminder for your upcoming appointment:\n\n🛎️ Service: ${service}\n📅 Date/Time: ${date_time}\n\nSee you soon! 🙏`
        await conn.sock.sendMessage(jid, { text: message })
        res.json({ success: true })
    } catch (err) {
        res.json({ success: false, error: err.message })
    }
})

// ─────────────────────────────────────────────
// SEND PDF BILL ENDPOINT
// ─────────────────────────────────────────────
app.post('/send-bill/:userId', async (req, res) => {
    const userId = req.params.userId
    const { order_id, customer_phone, customer_name } = req.body

    const conn = connections[userId]
    if (!conn || conn.status !== 'connected') {
        return res.json({ success: false, error: 'Bot not connected' })
    }

    try {
        const FLASK_URL = process.env.FLASK_URL || 'http://127.0.0.1:5000'
        const billRes = await axios.get(`${FLASK_URL}/api/generate-bill/${order_id}`, {
            responseType: 'arraybuffer'
        })

        const jid = customer_phone.replace(/\D/g, '') + '@s.whatsapp.net'
        await conn.sock.sendMessage(jid, {
            document: billRes.data,
            mimetype: 'application/pdf',
            fileName: `Order_${order_id}_Bill.pdf`,
            caption: `📄 Hi ${customer_name}! Here's your bill for Order #${order_id}. Thank you! 🙏`
        })

        res.json({ success: true })
    } catch (err) {
        res.json({ success: false, error: err.message })
    }
})

// ─────────────────────────────────────────────
// SEND ORDER STATUS UPDATE TO CUSTOMER
// ─────────────────────────────────────────────
app.post('/send-order-update/:userId', async (req, res) => {
    const userId = req.params.userId
    const { customer_phone, customer_name, order_id, status, items, total } = req.body

    const conn = connections[userId]
    if (!conn || conn.status !== 'connected') {
        return res.json({ success: false, error: 'Bot not connected' })
    }

    try {
        const jid = customer_phone.replace(/\D/g, '') + '@s.whatsapp.net'

        const statusEmojis = {
            'confirmed': '✅',
            'preparing': '👨‍🍳',
            'ready': '🎉',
            'delivered': '🚚',
            'cancelled': '❌'
        }

        const statusMessages = {
            'confirmed': 'Your order has been confirmed!',
            'preparing': 'We are preparing your order...',
            'ready': 'Your order is ready for pickup!',
            'delivered': 'Your order has been delivered!',
            'cancelled': 'Your order has been cancelled.'
        }

        const emoji = statusEmojis[status] || '📦'
        let message = `${emoji} *Order Update #${order_id}*\n\n${statusMessages[status] || 'Status: ' + status}\n\n💰 Total: ₹${total}\n\nThank you for choosing us! 🙏`

        await conn.sock.sendMessage(jid, { text: message })

        if (status === 'delivered') {
            setTimeout(async () => {
                try {
                    feedbackState[jid] = { awaiting_rating: true, customer_name: customer_name }
                    const fbMsg = `😊 Hi ${customer_name}! Thank you for ordering from *${config.business_name || 'us'}*!\n\nWe hope you enjoyed your order. Please rate us from *1 to 5* ⭐\n\nYour feedback helps us improve!`
                    await conn.sock.sendMessage(jid, { text: fbMsg })
                } catch (e) {}
            }, 30 * 60 * 1000)
        }

        res.json({ success: true })
    } catch (err) {
        res.json({ success: false, error: err.message })
    }
})

// ─────────────────────────────────────────────
// LIVE INBOX - SEND MANUAL MESSAGE
// ─────────────────────────────────────────────
app.post('/send-manual/:userId', async (req, res) => {
    const userId = req.params.userId
    const { phone, message } = req.body

    const conn = connections[userId]
    if (!conn || conn.status !== 'connected') {
        return res.json({ success: false, error: 'Bot not connected' })
    }

    try {
        const jid = phone.includes('@') ? phone : phone.replace(/\D/g, '') + '@s.whatsapp.net'
        await conn.sock.sendMessage(jid, { text: message })
        res.json({ success: true })
    } catch (err) {
        res.json({ success: false, error: err.message })
    }
})

// ─────────────────────────────────────────────
// LIVE INBOX - TOGGLE BOT
// ─────────────────────────────────────────────
app.post('/toggle-bot/:userId', (req, res) => {
    const { phone, paused } = req.body
    if (phone) {
        const jid = phone.includes('@') ? phone : phone.replace(/\D/g, '') + '@s.whatsapp.net'
        pauseState[jid] = paused
    }
    res.json({ success: true })
})

app.get('/bot-status/:userId/:phone', (req, res) => {
    const phone = req.params.phone
    const jid = phone.includes('@') ? phone : phone.replace(/\D/g, '') + '@s.whatsapp.net'
    res.json({ paused: !!pauseState[jid] })
})

// ─────────────────────────────────────────────
// START SERVER
// ─────────────────────────────────────────────
const PORT = process.env.PORT || 3000
app.listen(PORT, async () => {
    console.log(`🚀 Baileys service running on port ${PORT}`)
    setTimeout(restoreActiveBots, 3000)
})