const Groq = require('groq-sdk')
require('dotenv').config()

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY })

async function main() {
    try {
        console.log("Sending request to Groq via Node...")
        const completion = await groq.chat.completions.create({
            messages: [
                { role: 'user', content: 'Say hello!' }
            ],
            model: 'llama-3.3-70b-versatile',
            max_tokens: 50
        })
        console.log("Response:", completion.choices[0]?.message?.content)
    } catch (e) {
        console.error("Groq error:", e.message)
    }
}

main()
