import React, { useState } from 'react';
import {
  MessageSquarePlus,
  Settings,
  Sparkles,
  Info,
  CheckCircle2,
  XCircle,
  Copy,
  Check,
  ChevronDown,
  ChevronRight,
  Shield,
  Layers,
  Database
} from 'lucide-react';

export default function Sidebar({
  sessionId,
  onNewChat,
  systemPrompt,
  setSystemPrompt,
  simulationMode,
  setSimulationMode,
  health,
  isOpen,
  onClose
}) {
  const [showSettings, setShowSettings] = useState(false);
  const [copiedSession, setCopiedSession] = useState(false);

  const handleCopySession = () => {
    navigator.clipboard.writeText(sessionId);
    setCopiedSession(true);
    setTimeout(() => setCopiedSession(false), 2000);
  };

  return (
    <>
      {/* Mobile backdrop */}
      {isOpen && (
        <div
          onClick={onClose}
          className="fixed inset-0 bg-black/60 z-30 md:hidden backdrop-blur-sm"
        />
      )}

      <aside
        className={`fixed md:static inset-y-0 left-0 z-40 w-72 bg-charcoal-900 border-r border-charcoal-800 flex flex-col justify-between transition-transform duration-300 ease-in-out ${
          isOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
        }`}
      >
        <div className="flex flex-col h-full overflow-y-auto">
          {/* Header & Logo */}
          <div className="p-4 border-b border-charcoal-800 flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-brand-purple to-brand-blue flex items-center justify-center shadow-lg shadow-brand-purple/25">
                <Sparkles className="w-4 h-4 text-white" />
              </div>
              <div>
                <h1 className="font-bold text-base tracking-tight text-white flex items-center gap-1.5">
                  Lyftr AI
                  <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-brand-purple/20 text-brand-purple border border-brand-purple/30">
                    Pro
                  </span>
                </h1>
                <p className="text-[11px] text-slate-400">Intelligent AI Workspace</p>
              </div>
            </div>
          </div>

          {/* New Chat Button */}
          <div className="p-3">
            <button
              onClick={onNewChat}
              className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-gradient-to-r from-brand-purple/20 to-brand-blue/20 hover:from-brand-purple/30 hover:to-brand-blue/30 text-white font-medium text-sm border border-brand-purple/40 hover:border-brand-purple/60 transition-all shadow-md group"
            >
              <MessageSquarePlus className="w-4 h-4 text-brand-purple group-hover:scale-110 transition-transform" />
              <span>New Conversation</span>
            </button>
          </div>

          {/* Current Session Info Card */}
          <div className="px-3 py-2">
            <div className="p-3 rounded-xl bg-charcoal-850 border border-charcoal-750">
              <div className="flex items-center justify-between text-xs text-slate-400 mb-1.5">
                <span className="flex items-center gap-1">
                  <Database className="w-3.5 h-3.5 text-brand-purple" />
                  Active Session
                </span>
                <button
                  onClick={handleCopySession}
                  title="Copy session ID"
                  className="p-1 hover:text-white transition-colors"
                >
                  {copiedSession ? (
                    <Check className="w-3 h-3 text-emerald-400" />
                  ) : (
                    <Copy className="w-3 h-3 text-slate-400" />
                  )}
                </button>
              </div>
              <div className="font-mono text-[11px] text-slate-200 truncate bg-charcoal-900 px-2 py-1 rounded border border-charcoal-800">
                {sessionId}
              </div>
            </div>
          </div>

          {/* Settings Section */}
          <div className="px-3 py-2">
            <button
              onClick={() => setShowSettings(!showSettings)}
              className="w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium text-slate-300 hover:bg-charcoal-800/80 transition-colors"
            >
              <span className="flex items-center gap-2">
                <Settings className="w-4 h-4 text-slate-400" />
                Configuration & Controls
              </span>
              {showSettings ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
            </button>

            {showSettings && (
              <div className="mt-2 space-y-3 p-3 bg-charcoal-850/80 border border-charcoal-750 rounded-xl animate-fade-in text-xs">
                {/* System Prompt Input */}
                <div>
                  <label className="block text-[11px] font-semibold text-slate-300 mb-1">
                    System Prompt
                  </label>
                  <textarea
                    data-testid="system-prompt-input"
                    value={systemPrompt}
                    onChange={(e) => setSystemPrompt(e.target.value)}
                    rows={3}
                    placeholder="e.g. You are a senior software architect..."
                    className="w-full bg-charcoal-900 border border-charcoal-700 rounded-lg p-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-brand-purple/60 resize-none font-sans"
                  />
                </div>

                {/* Failover Simulation Header Selector */}
                <div>
                  <label className="block text-[11px] font-semibold text-slate-300 mb-1 flex items-center gap-1">
                    <Shield className="w-3.5 h-3.5 text-amber-400" />
                    Failover Simulation
                  </label>
                  <select
                    value={simulationMode}
                    onChange={(e) => setSimulationMode(e.target.value)}
                    className="w-full bg-charcoal-900 border border-charcoal-700 rounded-lg p-1.5 text-xs text-slate-200 focus:outline-none focus:border-brand-purple/60"
                  >
                    <option value="">Normal Operation (No Simulation)</option>
                    <option value="key_1=429">Simulate key_1 = 429 (Failover to key_2)</option>
                    <option value="key_1=500">Simulate key_1 = 500 (Failover to key_2)</option>
                    <option value="key_1=429,key_2=500">Simulate key_1=429, key_2=500 (All fail)</option>
                    <option value="*=429">Simulate *=429 (All keys fail 503)</option>
                  </select>
                  <p className="text-[10px] text-slate-500 mt-1">
                    Sends <code className="text-purple-300">X-Simulate-Failure</code> header.
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* System Specs & Architecture Info */}
          <div className="p-3 mt-auto">
            <div className="p-3 bg-charcoal-850/60 border border-charcoal-800 rounded-xl text-xs space-y-2">
              <div className="flex items-center gap-1.5 text-slate-300 font-medium">
                <Info className="w-3.5 h-3.5 text-blue-400" />
                Infrastructure
              </div>
              <div className="space-y-1 text-[11px] text-slate-400">
                <div className="flex justify-between">
                  <span>AI Engine:</span>
                  <span className="text-slate-200">LiteLLM + Gemini</span>
                </div>
                <div className="flex justify-between">
                  <span>Failover:</span>
                  <span className="text-slate-200">Automatic (Dual-key)</span>
                </div>
                <div className="flex justify-between">
                  <span>Caching:</span>
                  <span className="text-slate-200">LiteLLM Built-in</span>
                </div>
                <div className="flex justify-between">
                  <span>Database:</span>
                  <span className="text-slate-200">SQLite ONLY</span>
                </div>
              </div>
            </div>
          </div>

          {/* Health Status Footer */}
          <div className="p-3 border-t border-charcoal-800 flex items-center justify-between text-xs">
            <span className="text-slate-400">Backend API</span>
            {health?.status === 'ok' ? (
              <span className="inline-flex items-center gap-1 text-emerald-400 font-medium">
                <CheckCircle2 className="w-3.5 h-3.5" />
                Healthy ({health.keys_configured} keys)
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-rose-400 font-medium">
                <XCircle className="w-3.5 h-3.5" />
                Checking...
              </span>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}
