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

const connections  = {}
const qrCodes      = {}
const botConfigs   = {}
const bookingState = {}

// ─────────────────────────────────────────────
// AUTO-RESTORE on server start
// ─────────────────────────────────────────────
async function restoreActiveBots() {
    try {
        console.log('🔄 Checking for active bots to restore...')
        const response = await axios.get('http://127.0.0.1:5000/api/active-bots', { timeout: 5000 })
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
// CHECK LIMIT via Flask DB (single source of truth)
// Returns: { allowed: true/false, count, limit }
// Also increments count in DB when allowed
// ─────────────────────────────────────────────
async function checkAndIncrementLimit(userId) {
    try {
        const res = await axios.get(
            `http://127.0.0.1:5000/api/check-limit/${userId}`,
            { timeout: 3000 }
        )
        return res.data   // { allowed, count, limit, plan, message? }
    } catch (err) {
        console.log('⚠️ Limit check failed — allowing by default:', err.message)
        return { allowed: true, count: 0, limit: 999 }
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
        await axios.post(`http://127.0.0.1:5000/api/log-message/${userId}`, {
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
            `http://127.0.0.1:5000/api/save-booking/${userId}`,
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
5. Any natural question (e.g. "do you have parking?", "what are your timings?") → Answer helpfully using business info above
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
            connections[userId] = { status: 'connected', sock, botConfig }
            qrCodes[userId]     = null
            console.log(`✅ User ${userId} connected!`)

            // Notify owner
            try {
                const ownerNumber = botConfig.whatsapp_number || ''
                if (ownerNumber) {
                    const jid = ownerNumber.replace(/\D/g, '') + '@s.whatsapp.net'
                    await sock.sendMessage(jid, {
                        text: `✅ Your WhatsApp Bot is now ACTIVE!\n\n🤖 Bot: ${botConfig.bot_name || 'Your Bot'}\n🏢 Business: ${botConfig.business_name || ''}\n\nCustomers can now message you! 🚀`
                    })
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
                console.log(`🔄 Reconnecting user ${userId} in 3s...`)
                setTimeout(() => startConnection(userId, botConfigs[userId] || botConfig), 3000)
            } else {
                connections[userId] = { status: 'disconnected' }
                console.log(`❌ User ${userId} logged out`)
            }
        }
    })

    // ── INCOMING MESSAGES ──
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        try {
            if (type !== 'notify') return

            const msg = messages[0]
            if (!msg?.message || msg.key.fromMe) return

            const from = msg.key.remoteJid

            // ── SKIP GROUP MESSAGES (don't waste count) ──
            if (from.endsWith('@g.us')) {
                console.log(`⏭️ Skipping group message from ${from}`)
                return
            }

            const text = (
                msg.message?.conversation ||
                msg.message?.extendedTextMessage?.text ||
                msg.message?.imageMessage?.caption || ''
            ).trim()

            if (!text) return

            console.log(`\n📨 [User ${userId}] Message from ${from}: "${text}"`)

            // ── CHECK & INCREMENT LIMIT (DB) ──
            const limitData = await checkAndIncrementLimit(userId)

            if (!limitData.allowed) {
                console.log(`🚫 [User ${userId}] Daily limit reached! (${limitData.count}/${limitData.limit})`)
                await sock.sendMessage(from, {
                    text: `⚠️ Sorry, this bot has reached its daily message limit.\n\nPlease try again tomorrow or contact the business owner to upgrade their plan.`
                })
                return
            }

            console.log(`📊 [User ${userId}] Message count: ${limitData.count}/${limitData.limit} (${limitData.plan} plan)`)

            // ── GET LATEST CONFIG ──
            const config = botConfigs[userId] || {}

            // ── INIT BOOKING STATE for this customer ──
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

            // Log customer message
            await logMessage(userId, from, booking.customer_name, text, 'customer')

            const textLower       = text.toLowerCase()
            const bookingKeywords = ['book', 'booking', 'appointment', 'reserve', 'table']
            const isBookingIntent = bookingKeywords.some(kw => textLower.includes(kw))

            // ── BOOKING FLOW ──
            if (isBookingIntent && !booking.is_booking) {
                booking.is_booking = true
                const reply = `📅 Great! I'll help you book.\n\nPlease share:\n1️⃣ Your Name\n2️⃣ Service you want\n3️⃣ Preferred date & time\n4️⃣ Your phone number (10 digits)\n\nWe will confirm shortly! ✅`
                await sock.sendMessage(from, { text: reply })
                await logMessage(userId, from, booking.customer_name, reply, 'bot')
                return
            }

            if (booking.is_booking) {
                // Collect booking details
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

                // All details collected → save booking
                if (booking.customer_name && booking.customer_phone && booking.service && booking.date_time) {
                    await saveBooking(userId, {
                        customer_name:  booking.customer_name,
                        customer_phone: booking.customer_phone,
                        service:        booking.service,
                        date_time:      booking.date_time
                    })

                    const confirm = `✅ BOOKING CONFIRMED!\n\n👤 Name: ${booking.customer_name}\n🛎️ Service: ${booking.service}\n📅 Date/Time: ${booking.date_time}\n📱 Phone: ${booking.customer_phone}\n\nWe will contact you soon! Thank you! 🙏`
                    await sock.sendMessage(from, { text: confirm })
                    await logMessage(userId, from, booking.customer_name, confirm, 'bot')
                    delete bookingState[from]
                    return
                }

                // Missing details → ask again
                const missing = []
                if (!booking.customer_name)  missing.push('1️⃣ Name')
                if (!booking.service)        missing.push('2️⃣ Service')
                if (!booking.date_time)      missing.push('3️⃣ Date & Time')
                if (!booking.customer_phone) missing.push('4️⃣ Phone Number')

                const progress = `Got it! Still need:\n${missing.join('\n')}\n\nPlease share the missing details.`
                await sock.sendMessage(from, { text: progress })
                await logMessage(userId, from, booking.customer_name, progress, 'bot')
                return
            }

            // ── NORMAL AI REPLY (Friendly + Professional) ──
            const reply = await getAIReply(text, config)
            await sock.sendMessage(from, { text: reply })
            await logMessage(userId, from, booking.customer_name, reply, 'bot')

        } catch (err) {
            console.error('❌ Message handler error:', err.message)
        }
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

        // Delete auth so fresh QR on reconnect
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
app.listen(3000, async () => {
    console.log('🚀 Baileys service running on port 3000')
    setTimeout(restoreActiveBots, 3000)
})