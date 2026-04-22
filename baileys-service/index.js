require('dotenv').config()

const { default: makeWASocket, DisconnectReason, useMultiFileAuthState, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys')
const express = require('express')
const qrcode = require('qrcode')
const fs = require('fs')
const path = require('path')
const P = require('pino')
const Groq = require('groq-sdk')
const axios = require('axios')

const app = express()
app.use(express.json())

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY })

const FLASK_URL = process.env.FLASK_URL || 'http://127.0.0.1:5000'

const connections  = {}
const qrCodes      = {}
const botConfigs   = {}
const bookingState = {}

// ─────────────────────────────────────────────
// 🛡️ FIX 1: Random Human-like Delay
// ─────────────────────────────────────────────
function randomDelay(min = 3000, max = 7000) {
    const ms = Math.floor(Math.random() * (max - min + 1)) + min
    return new Promise(r => setTimeout(r, ms))
}

// ─────────────────────────────────────────────
// 🛡️ FIX 2: Send with Typing Indicator (sock fixed)
// ─────────────────────────────────────────────
async function sendWithTyping(sock, jid, text) {
    try {
        await sock.sendPresenceUpdate('composing', jid)
        const typingTime = Math.min(text.length * 50, 4000)
        await new Promise(r => setTimeout(r, typingTime))
        await sock.sendPresenceUpdate('paused', jid)
        await randomDelay(2000, 5000)
        await sock.sendMessage(jid, { text })
    } catch (err) {
        try {
            await sock.sendMessage(jid, { text })
        } catch (e) {
            console.error('❌ sendWithTyping failed:', e.message)
        }
    }
}

// ─────────────────────────────────────────────
// 🛡️ FIX 3: Message Queue — prevent simultaneous sends
// ─────────────────────────────────────────────
const messageQueues = {}

async function queueMessage(userId, fn) {
    if (!messageQueues[userId]) {
        messageQueues[userId] = Promise.resolve()
    }
    messageQueues[userId] = messageQueues[userId].then(fn).catch(err => {
        console.error(`❌ Queue error for ${userId}:`, err.message)
    })
    return messageQueues[userId]
}

// ─────────────────────────────────────────────
// 🛡️ FIX 4: Exponential Backoff Reconnect
// ─────────────────────────────────────────────
const reconnectAttempts = {}

function getReconnectDelay(userId) {
    const attempts = reconnectAttempts[userId] || 0
    // 3s → 6s → 12s → 24s → max 60s
    const delay = Math.min(3000 * Math.pow(2, attempts), 60000)
    reconnectAttempts[userId] = attempts + 1
    console.log(`🔄 Reconnect attempt ${attempts + 1} for user ${userId} — waiting ${delay/1000}s`)
    return delay
}

// ─────────────────────────────────────────────
// AUTO-RESTORE on server start
// ─────────────────────────────────────────────
async function restoreActiveBots() {
    try {
        console.log('🔄 Checking for active bots to restore...')
        const response = await axios.get(`${FLASK_URL}/api/active-bots`, { timeout: 5000 })
        const bots = response.data.bots || []

        if (bots.length === 0) {
            console.log('No active bots to restore.')
            return
        }

        console.log(`Found ${bots.length} active bot(s) — restoring...`)
        for (const bot of bots) {
            console.log(`🔁 Restoring bot for user ${bot.user_id}...`)
            await startConnection(bot.user_id, bot)
            await new Promise(r => setTimeout(r, 1000))
        }
    } catch (err) {
        console.log('⚠️ Could not restore bots:', err.message)
    }
}

// ─────────────────────────────────────────────
// CHECK & INCREMENT LIMIT via Flask DB
// ─────────────────────────────────────────────
async function checkAndIncrementLimit(userId) {
    try {
        const res = await axios.post(
            `${FLASK_URL}/api/check-limit/${userId}`,
            {},
            { timeout: 3000 }
        )
        return res.data
    } catch (err) {
        console.log('⚠️ Limit check failed — allowing by default:', err.message)
        return { allowed: true, count: 0, limit: 999 }
    }
}

// ─────────────────────────────────────────────
// CHECK BOOKING SLOT AVAILABILITY
// ─────────────────────────────────────────────
async function checkBookingSlot(userId, service, dateTime) {
    try {
        console.log(`🔍 Checking slot: ${service} at ${dateTime}`)
        const response = await axios.get(
            `${FLASK_URL}/api/check-booking-slot/${userId}`,
            {
                params: { service, date_time: dateTime },
                timeout: 5000
            }
        )
        console.log(`✅ Slot check result:`, response.data)
        return response.data
    } catch (err) {
        console.error('❌ Slot check failed:', err.message)
        return { available: true }
    }
}

// ─────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────
function isPhoneNumber(text) {
    return /^\d{10}$/.test(text.trim())
}

function isDatePattern(text) {
    const patterns = [
        /\d{1,2}-\d{1,2}-\d{4}/,
        /\d{1,2}\/\d{1,2}\/\d{4}/,
        /\d{1,2}[:\.]\d{2}\s*(am|pm)?/i,
        /today|tomorrow|morning|afternoon|evening|night|\d+(st|nd|rd|th)|january|february|march|april|may|june|july|august|september|october|november|december/i
    ]
    return patterns.some(p => p.test(text))
}

// ─────────────────────────────────────────────
// AI: Extract service name from user message
// ─────────────────────────────────────────────
async function extractServiceWithAI(userMessage, botConfig) {
    try {
        const prompt = `You are a service extractor for a booking system.

Available services:
${botConfig.services}

User said: "${userMessage}"

TASK: If the user mentioned ANY service from the list above, extract and return ONLY the exact service name.
If no service mentioned, return: "NOT_FOUND"

Return ONLY the service name or "NOT_FOUND", nothing else.`

        const completion = await groq.chat.completions.create({
            messages: [{ role: 'user', content: prompt }],
            model: 'llama-3.3-70b-versatile',
            max_tokens: 100
        })

        const result = completion.choices[0]?.message?.content?.trim() || 'NOT_FOUND'
        console.log(`🎯 Service extracted: "${userMessage}" → "${result}"`)
        return result !== 'NOT_FOUND' ? result : null
    } catch (err) {
        console.error('Service extraction error:', err.message)
        return null
    }
}

// ─────────────────────────────────────────────
// LOG MESSAGE to Flask DB
// ─────────────────────────────────────────────
async function logMessage(userId, customerPhone, customerName, messageText, sender) {
    try {
        await axios.post(`${FLASK_URL}/api/log-message/${userId}`, {
            customer_phone: customerPhone,
            customer_name:  customerName,
            message_text:   messageText,
            sender:         sender
        }, { timeout: 5000 })
    } catch (err) {
        console.error('❌ Log message failed:', err.message)
    }
}

// ─────────────────────────────────────────────
// SAVE BOOKING to Flask DB
// ─────────────────────────────────────────────
async function saveBooking(userId, bookingData) {
    try {
        const response = await axios.post(
            `${FLASK_URL}/api/save-booking/${userId}`,
            bookingData,
            { timeout: 5000 }
        )
        console.log(`✅ Booking saved! ID: ${response.data.booking_id}`)
        return response.data
    } catch (err) {
        console.error('❌ Save booking failed:', err.message)
        return null
    }
}

// ─────────────────────────────────────────────
// AI REPLY — Friendly + Professional
// ─────────────────────────────────────────────
async function getAIReply(userMessage, botConfig) {
    try {
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

SERVICES/PRODUCTS AVAILABLE:
${botConfig.services || 'No services listed yet'}

YOUR PERSONALITY:
- Friendly, warm and welcoming 😊
- Professional and helpful at all times
- Keep replies short, clear and easy to read
- Use 1-2 emojis per message (not too many)
- Never be rude, negative or dismissive

REPLY RULES:
1. Greeting (hi/hello/hey/start/hy) → Reply with warm welcome + menu:
"👋 Welcome to ${botConfig.business_name || 'our business'}!

How can I help you today? Type:
📋 SERVICES — View our services & prices
📅 BOOK — Make a booking or appointment
📍 INFO — Location & timings
📞 CONTACT — Get in touch with us"

2. SERVICES or menu → List ALL services with prices clearly formatted
3. INFO → Show address and timings in a clean format
4. BOOK → Guide them to start the booking process
5. Any natural question → Answer helpfully using business info above
6. If you don't know the answer → Say: "For more details, please contact us directly — we're happy to help! 😊"
7. Always reply in the SAME language the customer uses (Tamil, English, Hindi etc.)
8. Never make up services, prices or information not listed above
9. Never mention you are an AI — just be a helpful business assistant`

        const completion = await groq.chat.completions.create({
            messages: [
                { role: 'system', content: systemPrompt },
                { role: 'user',   content: userMessage }
            ],
            model: 'llama-3.3-70b-versatile',
            max_tokens: 500
        })

        return completion.choices[0]?.message?.content || '👋 Hello! How can I help you?'
    } catch (err) {
        console.error('Groq error:', err.message)
        return '👋 Hello! How can I help you today?'
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
    if (!fs.existsSync(authFolder)) {
        fs.mkdirSync(authFolder, { recursive: true })
    }

    const { state, saveCreds } = await useMultiFileAuthState(authFolder)
    const { version }          = await fetchLatestBaileysVersion()

    const sock = makeWASocket({
        version,
        auth:              state,
        printQRInTerminal: false,
        logger:            P({ level: 'silent' })
    })

    sock.ev.on('creds.update', saveCreds)

    // ── CONNECTION UPDATES ──
    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update

        if (qr) {
            console.log(`📱 QR generated for user ${userId}`)
            const qrImage = await qrcode.toDataURL(qr)
            qrCodes[userId]      = qrImage
            connections[userId]  = { status: 'qr_ready', sock, botConfig }
        }

        if (connection === 'open') {
            // 🛡️ FIX 4: Reset reconnect attempts on successful connect
            reconnectAttempts[userId] = 0

            connections[userId] = { status: 'connected', sock, botConfig }
            qrCodes[userId]     = null
            console.log(`✅ User ${userId} connected!`)

            try {
                const ownerNumber = botConfig.whatsapp_number || ''
                if (ownerNumber) {
                    const jid = ownerNumber.replace(/\D/g, '') + '@s.whatsapp.net'
                    await sendWithTyping(sock, jid,
                        `✅ Your WhatsApp Bot is now ACTIVE!\n\n🤖 Bot: ${botConfig.bot_name || 'Your Bot'}\n🏢 Business: ${botConfig.business_name || ''}\n\nCustomers can now message you! 🚀`
                    )
                }
            } catch (err) {
                console.error('Owner notification error:', err.message)
            }
        }

        if (connection === 'close') {
            const code            = lastDisconnect?.error?.output?.statusCode
            const shouldReconnect = code !== DisconnectReason.loggedOut

            console.log(`🔴 Connection closed for user ${userId}, code: ${code}`)

            if (shouldReconnect) {
                connections[userId] = { status: 'reconnecting' }
                // 🛡️ FIX 4: Exponential backoff instead of fixed 3s
                const delay = getReconnectDelay(userId)
                setTimeout(() => startConnection(userId, botConfigs[userId] || botConfig), delay)
            } else {
                connections[userId] = { status: 'disconnected' }
                reconnectAttempts[userId] = 0
                console.log(`❌ User ${userId} logged out`)
            }
        }
    })

    // ── INCOMING MESSAGES ──
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return

        const msg = messages[0]
        if (!msg?.message || msg.key.fromMe) return

        const from = msg.key.remoteJid

        // 🛡️ FIX 5: Skip group messages AND status/broadcast messages
        if (from.endsWith('@g.us')) {
            console.log(`⏭️ Skipping group message`)
            return
        }
        if (from === 'status@broadcast' || from.endsWith('@broadcast')) {
            console.log(`⏭️ Skipping broadcast/status message`)
            return
        }

        const text = (
            msg.message?.conversation ||
            msg.message?.extendedTextMessage?.text ||
            msg.message?.imageMessage?.caption || ''
        ).trim()

        if (!text) return

        console.log(`\n📨 [User ${userId}] Message from ${from}: "${text}"`)

        // 🛡️ FIX 3: Queue messages per user — no simultaneous processing
        await queueMessage(userId, async () => {
            try {
                // 🛡️ Mark message as READ
                await sock.readMessages([msg.key])

                // 🛡️ Small pause after reading
                await randomDelay(1000, 2500)

                // ── CHECK & INCREMENT LIMIT ──
                const limitData = await checkAndIncrementLimit(userId)

                if (!limitData.allowed) {
                    console.log(`🚫 [User ${userId}] Daily limit reached!`)
                    // 🛡️ FIX 2: sock passed correctly (was missing before!)
                    await sendWithTyping(sock, from, `⚠️ Sorry, this bot has reached its daily message limit.\n\nPlease try again tomorrow or contact the business owner to upgrade their plan.`)
                    return
                }

                console.log(`📊 [User ${userId}] Message count: ${limitData.count}/${limitData.limit} (${limitData.plan} plan)`)

                const config = botConfigs[userId] || {}

                if (!bookingState[from]) {
                    bookingState[from] = {
                        user_id:        userId,
                        customer_name:  null,
                        customer_phone: null,
                        service:        null,
                        date_time:      null,
                        is_booking:     false
                    }
                }
                const booking = bookingState[from]

                await logMessage(userId, from, booking.customer_name, text, 'customer')

                const textLower       = text.toLowerCase()
                const bookingKeywords = ['book', 'booking', 'appointment', 'reserve', 'table']
                const isBookingIntent = bookingKeywords.some(kw => textLower.includes(kw))

                // ── BOOKING FLOW ──
                if (isBookingIntent && !booking.is_booking) {
                    booking.is_booking = true
                    const reply = `📅 Great! I'll help you book.\n\nPlease share:\n1️⃣ Your Name\n2️⃣ Service you want\n3️⃣ Preferred date & time\n4️⃣ Your phone number (10 digits)\n\nWe will confirm shortly! ✅`
                    await sendWithTyping(sock, from, reply)
                    await logMessage(userId, from, booking.customer_name, reply, 'bot')
                    return
                }

                if (booking.is_booking) {
                    if (isPhoneNumber(text) && !booking.customer_phone) {
                        booking.customer_phone = text.trim()
                    }
                    if (isDatePattern(text) && !booking.date_time) {
                        booking.date_time = text.trim()
                    }
                    if (!booking.service) {
                        const extracted = await extractServiceWithAI(text, config)
                        if (extracted) booking.service = extracted
                    }
                    if (!booking.customer_name && !isPhoneNumber(text) && !isDatePattern(text) && text.length < 50) {
                        if (!text.includes(' at ') && !text.includes(' on ') && !text.includes('booking')) {
                            booking.customer_name = text.trim()
                        }
                    }

                    if (booking.customer_name && booking.customer_phone && booking.service && booking.date_time) {
                        const slotCheck = await checkBookingSlot(userId, booking.service, booking.date_time)

                        if (!slotCheck.available) {
                            const availableSlots = slotCheck.available_slots?.join(', ') || 'Please try a different time'
                            const slotMsg = `❌ Sorry! This time slot (${booking.date_time}) is already booked.\n\n⏰ Available times:\n${availableSlots}\n\nPlease choose another time! 😊`
                            await sendWithTyping(sock, from, slotMsg)
                            await logMessage(userId, from, booking.customer_name, slotMsg, 'bot')
                            booking.date_time = null
                            const retry = `📅 Please share a different date & time.`
                            await sendWithTyping(sock, from, retry)
                            await logMessage(userId, from, booking.customer_name, retry, 'bot')
                            return
                        }

                        await saveBooking(userId, {
                            customer_name:  booking.customer_name,
                            customer_phone: booking.customer_phone,
                            service:        booking.service,
                            date_time:      booking.date_time
                        })

                        const confirm = `✅ BOOKING CONFIRMED!\n\n👤 Name: ${booking.customer_name}\n🛎️ Service: ${booking.service}\n📅 Date/Time: ${booking.date_time}\n📱 Phone: ${booking.customer_phone}\n\nWe will contact you soon! Thank you! 🙏`
                        await sendWithTyping(sock, from, confirm)
                        await logMessage(userId, from, booking.customer_name, confirm, 'bot')
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

                // ── NORMAL AI REPLY ──
                const reply = await getAIReply(text, config)
                await sendWithTyping(sock, from, reply)
                await logMessage(userId, from, booking.customer_name, reply, 'bot')

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
    const qr     = qrCodes[userId]
    const status = connections[userId]?.status || 'not_started'
    res.json({ qr: qr || null, status })
})

app.get('/status/:userId', (req, res) => {
    const userId = req.params.userId
    res.json({ status: connections[userId]?.status || 'not_started' })
})

app.get('/disconnect/:userId', async (req, res) => {
    const userId = req.params.userId
    try {
        const conn = connections[userId]
        if (conn?.sock) {
            try { await conn.sock.logout() } catch (e) {}
            try { conn.sock.end()          } catch (e) {}
        }

        connections[userId] = { status: 'disconnected' }
        qrCodes[userId]     = null
        delete botConfigs[userId]
        reconnectAttempts[userId] = 0

        const authFolder = path.join(__dirname, 'auth', userId.toString())
        if (fs.existsSync(authFolder)) {
            fs.rmSync(authFolder, { recursive: true, force: true })
            console.log(`🗑️ Auth deleted for user ${userId}`)
        }

        console.log(`✅ User ${userId} disconnected`)
        res.json({ success: true })
    } catch (err) {
        console.error('Disconnect error:', err.message)
        res.json({ success: false, error: err.message })
    }
})

// ─────────────────────────────────────────────
// START SERVER
// ─────────────────────────────────────────────
const PORT = process.env.PORT || 3000
app.listen(PORT, async () => {
    console.log(`🚀 Baileys service running on port ${PORT}`)
    setTimeout(restoreActiveBots, 3000)
})
