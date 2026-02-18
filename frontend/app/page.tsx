"use client";

import { useState, useEffect } from "react";

export default function Home() {
  const [messages, setMessages] = useState([]);

  useEffect(() => {
    const sse = new EventSource(
      "http://127.0.0.1:8000/api/livestream/stream",
      { withCredentials: true }
    );

    sse.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data.replace("data: ", ""));

        // ✅ Store every message
        setMessages((prev) => [...prev, data]);

      } catch (err) {
        console.error("Invalid JSON:", event.data);
      }
    };

    return () => {
      sse.close();
    };
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex min-h-screen w-full max-w-3xl flex-col items-center justify-between py-32 px-16 bg-white dark:bg-black sm:items-start">
        <header className="mb-8 text-2xl font-bold">
          Home Page
        </header>

        {/* Display messages from SSE */}
        <section className="w-full">
          {messages.length === 0 ? (
            <p>No messages yet</p>
          ) : (
            messages.map((msg, index) => (
              <pre key={index}>
                {JSON.stringify(msg, null, 2)}
              </pre>
            ))
          )}
        </section>
      </main>
    </div>
  );
}
