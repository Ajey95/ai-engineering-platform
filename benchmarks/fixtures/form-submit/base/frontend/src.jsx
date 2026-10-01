import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'

function App() {
  const [name, setName] = useState('')
  const [message, setMessage] = useState('')
  async function submit(event) {
    event.preventDefault()
    const response = await fetch('/api/tickets', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_name: name }),
    })
    setMessage(response.ok ? 'Ticket created' : 'Could not create ticket')
  }
  return <main style={{fontFamily:'system-ui',maxWidth:500,margin:'80px auto'}}>
    <h1>Create ticket</h1>
    <form onSubmit={submit}>
      <label>Item name <input value={name} onChange={event => setName(event.target.value)} required /></label>
      <button type="submit">Create</button>
    </form>
    <p role="status">{message}</p>
  </main>
}

createRoot(document.getElementById('root')).render(<App />)
