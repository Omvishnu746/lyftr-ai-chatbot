import React, { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import ChatWindow from './components/ChatWindow';

// Support VITE_API_BASE_URL for Vercel/Railway deployments
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

function generateSessionId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `s-${crypto.randomUUID()}`;
  }
  return `s-${Math.random().toString(36).substring(2, 10)}`;
}

export default function App() {
  const [sessionId, setSessionId] = useState(generateSessionId());
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [simulationMode, setSimulationMode] = useState('');
  const [health, setHealth] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Check backend health
  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/health`);
      if (res.ok) {
        const data = await res.json();
        setHealth(data);
      } else {
        setHealth({ status: 'error' });
      }
    } catch (err) {
      setHealth({ status: 'offline' });
    }
  }, []);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  // Start new conversation / new session
  const handleNewChat = () => {
    const newId = generateSessionId();
    setSessionId(newId);
    setMessages([]);
    setInput('');
    setError(null);
    setSidebarOpen(false);
  };

  // Send message to backend
  const handleSendMessage = async () => {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;

    // Append user message locally
    const userMsg = {
      role: 'user',
      content: trimmed
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setIsLoading(true);
    setError(null);

    try {
      const headers = {
        'Content-Type': 'application/json'
      };

      if (simulationMode) {
        headers['X-Simulate-Failure'] = simulationMode;
      }

      // Send ONLY newly entered message and optional system prompt
      const payload = {
        session_id: sessionId,
        message: trimmed,
        ...(systemPrompt.trim() ? { system_prompt: systemPrompt.trim() } : {})
      };

      const res = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload)
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || `Request failed with status ${res.status}`);
      }

      // Append assistant reply with metadata
      const assistantMsg = {
        role: 'assistant',
        content: data.reply,
        metadata: data.metadata
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      setError(err.message || 'An unexpected error occurred while processing your request.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex h-screen w-screen bg-charcoal-950 text-slate-100 font-sans overflow-hidden">
      <Sidebar
        sessionId={sessionId}
        onNewChat={handleNewChat}
        systemPrompt={systemPrompt}
        setSystemPrompt={setSystemPrompt}
        simulationMode={simulationMode}
        setSimulationMode={setSimulationMode}
        health={health}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <ChatWindow
        messages={messages}
        input={input}
        setInput={setInput}
        onSend={handleSendMessage}
        isLoading={isLoading}
        error={error}
        sessionId={sessionId}
        onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
      />
    </div>
  );
}
