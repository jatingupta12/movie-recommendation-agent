import { useEffect, useRef, useState } from 'react';

const QUICK_PROMPTS = [
  { icon: '◒', label: 'Something eerie but not too gory', prompt: 'Suggest an eerie horror movie that is not too gory.' },
  { icon: '✦', label: 'A clever sci-fi movie', prompt: 'Recommend a clever science fiction movie for tonight.' },
  { icon: '⌁', label: 'A short, gripping series', prompt: 'Find me a gripping TV series with episodes I can start tonight.' },
];

const SECTIONS = [
  { key: 'recommended', title: 'Good bets', icon: '✦', tone: 'gold' },
  { key: 'new_this_week', title: 'New this week', icon: '↗', tone: 'coral' },
  { key: 'hidden_gems', title: 'Under the radar', icon: '✧', tone: 'lavender' },
];

function App() {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [serviceStatus, setServiceStatus] = useState('checking');
  const [error, setError] = useState('');
  const [limit, setLimit] = useState(8);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    fetch('/health').then((response) => {
      if (!response.ok) throw new Error('offline');
      return response.json();
    }).then(() => setServiceStatus('online')).catch(() => setServiceStatus('offline'));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, busy]);

  async function sendMessage(text = draft) {
    const prompt = text.trim();
    if (!prompt || busy) return;
    const messageId = `${Date.now()}-${Math.random()}`;
    setMessages((current) => [...current, { id: messageId, role: 'user', text: prompt }]);
    setDraft('');
    setError('');
    setBusy(true);
    try {
      const response = await fetch('/api/weekend-digest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request: prompt, limit }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'The recommendation service could not answer right now.');
      setMessages((current) => [...current, { id: `${messageId}-reply`, role: 'assistant', digest: data.digest }]);
      setServiceStatus('online');
    } catch (requestError) {
      setError(requestError instanceof TypeError
        ? 'I can’t reach Weekend Watch. Start the local API, then try again.'
        : requestError.message);
      setServiceStatus('offline');
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    sendMessage();
  }

  function handleDraftChange(event) {
    setDraft(event.target.value);
    event.target.style.height = 'auto';
    event.target.style.height = `${Math.min(event.target.scrollHeight, 130)}px`;
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }

  const hasMessages = messages.length > 0;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="Weekend Watch home">
          <span className="brand-mark"><span /></span>
          <span className="brand-name">weekend<span>watch</span></span>
        </a>
        <button className="new-chat" onClick={() => { setMessages([]); setError(''); inputRef.current?.focus(); }}>
          <span className="plus-icon">＋</span> New conversation
        </button>
        <div className="sidebar-label">YOUR SPACE</div>
        <button className="nav-item active"><span className="nav-icon">▤</span> Movie chat</button>
        <div className="sidebar-label prompt-label">TRY ASKING</div>
        <div className="sidebar-prompts">
          {QUICK_PROMPTS.map((item) => (
            <button key={item.label} className="side-prompt" onClick={() => sendMessage(item.prompt)} disabled={busy}>
              <span>{item.icon}</span>{item.label}
            </button>
          ))}
        </div>
        <div className="sidebar-bottom">
          <div className={`connection ${serviceStatus}`}>
            <span className="connection-dot" />
            <span>{serviceStatus === 'checking' ? 'Connecting…' : serviceStatus === 'online' ? 'Agent is ready' : 'Agent is offline'}</span>
          </div>
          <div className="profile-card">
            <div className="avatar">🍿</div>
            <div><strong>Your movie pal</strong><span>Personal watchlist</span></div>
            <span className="profile-menu">···</span>
          </div>
        </div>
      </aside>

      <main className="main-panel" id="top">
        <header className="topbar">
          <div className="mobile-brand"><span className="brand-mark"><span /></span> weekend<span>watch</span></div>
          <div className="topbar-context"><span className="context-dot" /> Your personal movie guide</div>
          <button className="topbar-button" onClick={() => inputRef.current?.focus()} aria-label="Focus message box">⌘ <span>K</span></button>
        </header>

        <section className={`conversation ${hasMessages ? 'has-messages' : 'empty-conversation'}`} aria-live="polite">
          {!hasMessages ? (
            <div className="welcome">
              <div className="welcome-eyebrow"><span className="sparkle">✦</span> YOUR NEXT FAVORITE IS OUT THERE</div>
              <h1>What are you<br />in the mood for<span>?</span></h1>
              <p className="welcome-copy">Tell me what you feel like watching. I’ll find movies and shows that fit your taste and what’s available to stream.</p>
              <div className="prompt-grid">
                {QUICK_PROMPTS.map((item, index) => (
                  <button key={item.label} className={`prompt-card prompt-${index + 1}`} onClick={() => sendMessage(item.prompt)} disabled={busy}>
                    <span className="prompt-card-icon">{item.icon}</span><span>{item.label}</span><span className="prompt-arrow">↗</span>
                  </button>
                ))}
              </div>
              <div className="trust-note"><span className="mini-sparkle">✳</span> Recommendations based on real ratings and streaming availability</div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <div className={`message-row ${message.role}`} key={message.id}>
                  {message.role === 'assistant' && <div className="assistant-avatar"><span className="brand-mark small"><span /></span></div>}
                  <div className="message-content">
                    {message.role === 'user' ? <div className="user-bubble">{message.text}</div> : <RecommendationResults digest={message.digest} />}
                  </div>
                </div>
              ))}
              {busy && <div className="message-row assistant"><div className="assistant-avatar"><span className="brand-mark small"><span /></span></div><div className="thinking-card"><span className="typing-dots"><i /><i /><i /></span><span>Finding your next watch…</span></div></div>}
              {error && <div className="error-banner" role="alert"><span>!</span>{error}</div>}
              <div ref={bottomRef} />
            </div>
          )}
        </section>

        <footer className={`composer-wrap ${hasMessages ? 'with-history' : ''}`}>
          {error && !hasMessages && <div className="error-banner composer-error" role="alert"><span>!</span>{error}</div>}
          <form className="composer" onSubmit={handleSubmit}>
            <textarea ref={inputRef} value={draft} onChange={handleDraftChange} onKeyDown={handleKeyDown}
              placeholder="Ask for a movie, a mood, a genre…" aria-label="Ask for a movie or TV recommendation" rows={1} disabled={busy} />
            <div className="composer-controls">
              <div className="composer-hint"><span className="hint-sparkle">✦</span> Be as specific as you like</div>
              <div className="composer-actions">
                <label className="limit-select" title="Maximum titles in the response"><span>Up to</span>
                  <select value={limit} onChange={(event) => setLimit(Number(event.target.value))} aria-label="Maximum recommendations">
                    <option value={4}>4</option><option value={6}>6</option><option value={8}>8</option><option value={10}>10</option>
                  </select>
                </label>
                <button className="send-button" type="submit" disabled={!draft.trim() || busy} aria-label="Send message">
                  {busy ? <span className="send-spinner" /> : <span>↑</span>}
                </button>
              </div>
            </div>
          </form>
          <p className="disclaimer">Movie facts and availability come from TMDB and Watchmode. Explanations are personalized.</p>
        </footer>
      </main>
    </div>
  );
}

function RecommendationResults({ digest }) {
  const sections = SECTIONS.filter((section) => digest?.[section.key]?.length);
  const totalTitles = sections.reduce((count, section) => count + digest[section.key].length, 0);
  if (!totalTitles) {
    return <div className="results-block"><div className="results-intro"><span className="result-icon empty">⌕</span><div><strong>I didn’t find a match this time.</strong><p>Try another genre, mood, or type of movie.</p></div></div></div>;
  }
  return (
    <div className="results-block">
      <div className="results-intro"><span className="result-icon">✦</span><div><strong>I found a few that fit.</strong><p>Here are some picks to match your mood.</p></div><span className="result-count">{totalTitles} PICKS</span></div>
      <div className="recommendation-sections">
        {sections.map((section) => (
          <section className="recommendation-section" key={section.key}>
            <h2 className={`section-title ${section.tone}`}><span>{section.icon}</span>{section.title}</h2>
            <div className="title-grid">{digest[section.key].map((title) => <TitleCard title={title} key={`${title.tmdb_id}-${section.key}`} />)}</div>
          </section>
        ))}
      </div>
      <div className="results-footnote"><span>✓</span> Already watched and not-interested titles are left out.</div>
    </div>
  );
}

function TitleCard({ title }) {
  const isMovie = title.media_type === 'movie';
  return (
    <article className="title-card">
      <div className="title-card-topline"><span className="media-label">{isMovie ? 'FILM' : 'SERIES'}</span>
        <span className="rating">{title.rating == null ? <span className="muted-rating">Rating n/a</span> : <>★ <b>{Number(title.rating).toFixed(1)}</b></>}</span>
      </div>
      <h3>{title.title}</h3>
      <div className="title-meta">{title.release_date?.slice(0, 4) || 'Year unavailable'}{title.genres?.length > 0 && <><span>·</span>{title.genres.slice(0, 2).join(' / ')}</>}</div>
      <p className="title-synopsis">{title.synopsis || 'Synopsis unavailable from TMDB.'}</p>
      <div className="match-reason"><span>✧</span><span>{title.why_it_matches}</span></div>
      <div className="title-card-footer">
        {title.streaming_services?.length ? <div className="streaming-list"><span className="stream-icon">▶</span>{title.streaming_services.slice(0, 3).join(' · ')}</div>
          : <div className="availability-unknown"><span className="stream-icon">◌</span>Availability not confirmed</div>}
      </div>
    </article>
  );
}

export default App;
