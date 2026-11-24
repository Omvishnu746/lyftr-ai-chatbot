import React from 'react';
import { Zap, Server, Clock, Database, ShieldAlert, Cpu } from 'lucide-react';

export default function Metadata({ metadata }) {
  if (!metadata) return null;

  const isCache = metadata.resolution_source === 'LLM_CACHE';

  return (
    <div className="mt-3 pt-2.5 border-t border-charcoal-750/60 flex flex-wrap items-center gap-2 text-xs">
      {/* Resolution Source */}
      <div
        data-testid="meta-source"
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md font-medium border transition-colors ${
          isCache
            ? 'bg-emerald-950/40 text-emerald-300 border-emerald-800/40'
            : 'bg-brand-purple/10 text-purple-300 border-brand-purple/30'
        }`}
      >
        {isCache ? (
          <Database className="w-3.5 h-3.5 text-emerald-400" />
        ) : (
          <Zap className="w-3.5 h-3.5 text-brand-purple" />
        )}
        <span>{metadata.resolution_source}</span>
      </div>

      {/* Model Name */}
      <div
        data-testid="meta-model"
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-charcoal-850 text-slate-300 border border-charcoal-700/60"
      >
        <Cpu className="w-3.5 h-3.5 text-blue-400" />
        <span>{metadata.model || 'gemini/gemini-2.0-flash'}</span>
      </div>

      {/* Latency */}
      <div
        data-testid="meta-latency"
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-charcoal-850 text-slate-300 border border-charcoal-700/60"
      >
        <Clock className="w-3.5 h-3.5 text-amber-400" />
        <span>{metadata.latency_ms} ms</span>
      </div>

      {/* Input Tokens */}
      <div
        data-testid="meta-input-tokens"
        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-charcoal-850 text-slate-300 border border-charcoal-700/60"
        title="Input prompt tokens"
      >
        <span className="text-slate-400">In:</span>
        <span className="font-semibold text-slate-200">{metadata.input_tokens}</span>
      </div>

      {/* Output Tokens */}
      <div
        data-testid="meta-output-tokens"
        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-charcoal-850 text-slate-300 border border-charcoal-700/60"
        title="Output response tokens"
      >
        <span className="text-slate-400">Out:</span>
        <span className="font-semibold text-slate-200">{metadata.output_tokens}</span>
      </div>

      {/* Key ID badge */}
      {metadata.key_id ? (
        <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] bg-slate-800/80 text-slate-300 border border-slate-700/50">
          <Server className="w-3 h-3 text-cyan-400" />
          <span>{metadata.key_id}</span>
        </div>
      ) : isCache ? (
        <span className="text-[11px] text-slate-500 italic">Key: N/A (Cached)</span>
      ) : null}

      {/* Fallback Attempts indicator */}
      {metadata.fallback_attempts > 0 && (
        <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] bg-amber-950/50 text-amber-300 border border-amber-800/50">
          <ShieldAlert className="w-3 h-3 text-amber-400" />
          <span>Failover ({metadata.fallback_attempts})</span>
        </div>
      )}
    </div>
  );
}
