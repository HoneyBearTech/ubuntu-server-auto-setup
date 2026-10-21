// A run's output: the log so far, then each new line as the server streams it (Server-Sent Events from
// /runs/<id>/stream). Ansible's status lines are coloured, and the text says the same ("ok:", "fatal:"), so
// colour is never the only sign. Follow keeps the newest line in view; scrolling up stops following.

import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { plural } from "../format";

const CHUNK = 400;

export function lineClass(line: string): string {
  if (/^PLAY (RECAP )?\[|^PLAY RECAP/.test(line)) return "l-play";
  if (/^(TASK|RUNNING HANDLER) \[/.test(line)) return "l-task";
  if (/^ok: /.test(line)) return "l-ok";
  if (/^changed: /.test(line)) return "l-changed";
  if (/^skipping: /.test(line)) return "l-skip";
  if (/^FAILED - RETRYING|^\.\.\.ignoring/.test(line)) return "l-muted";
  if (/^(fatal|failed|unreachable): |^ERROR!|^\[ERROR\]|^An exception occurred/i.test(line)) return "l-error";
  if (/^\[(WARNING|DEPRECATION WARNING)\]/.test(line)) return "l-warn";
  return "";
}

const RECAP = /^(\S+)(\s+:\s+)(ok=\d+.*)$/;
const COUNT_CLASS: Record<string, string> = {
  ok: "l-ok", changed: "l-changed", unreachable: "l-error", failed: "l-error", skipped: "l-skip",
  rescued: "l-muted", ignored: "l-muted",
};

// A PLAY RECAP host line, with each count coloured when it isn't zero.
function Recap({ match }: { match: RegExpExecArray }) {
  const parts = match[3].split(/(\s+)/);
  return (
    <>
      <span className="l-task">{match[1]}</span>{match[2]}
      {parts.map((part, i) => {
        const [name, value] = part.split("=");
        const cls = value && Number(value) > 0 ? COUNT_CLASS[name] : undefined;
        return cls ? <span key={i} className={cls}>{part}</span> : part;
      })}
    </>
  );
}

function Line({ text }: { text: string }) {
  const recap = RECAP.exec(text);
  if (recap) return <span className="line"><Recap match={recap} />{"\n"}</span>;
  const cls = lineClass(text);
  return <span className="line">{cls ? <span className={cls}>{text}</span> : text}{"\n"}</span>;
}

// Lines in blocks: a block that is complete never draws again, so a long log stays quick to update.
const Block = memo(function Block({ lines }: { lines: string[]; start: number }) {
  return <>{lines.map((text, i) => <Line key={i} text={text} />)}</>;
}, (a, b) => a.start === b.start && a.lines.length === b.lines.length);

export function splitLines(text: string): string[] {
  if (!text) return [];
  const lines = text.replace(/\r/g, "").split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

export function LogView({ initial, stream, onEnd }: {
  initial: string;
  stream: string | null; // the stream's URL while the run is going
  onEnd?: (status: string) => void;
}) {
  const pre = useRef<HTMLPreElement>(null);
  const lines = useRef<string[]>(splitLines(initial));
  const [count, setCount] = useState(lines.current.length);
  const [follow, setFollow] = useState(true);
  const [wrap, setWrap] = useState(false);
  const onEndRef = useRef(onEnd);
  useEffect(() => {
    onEndRef.current = onEnd;
  }, [onEnd]);

  useEffect(() => {
    if (!stream || typeof EventSource === "undefined") return undefined;
    const source = new EventSource(stream);
    let queued = false;
    const flush = () => {
      queued = false;
      setCount(lines.current.length);
    };
    source.onmessage = (event: MessageEvent<string>) => {
      lines.current.push(event.data);
      if (!queued) {
        queued = true;
        (window.requestAnimationFrame ?? ((f: () => void) => setTimeout(f, 16)))(flush);
      }
    };
    source.addEventListener("end", (event) => {
      source.close();
      flush();
      onEndRef.current?.((event as MessageEvent<string>).data);
    });
    return () => source.close();
  }, [stream]);

  useLayoutEffect(() => {
    const el = pre.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [count, follow, wrap]);

  const onScroll = () => {
    const el = pre.current;
    if (el && follow && el.scrollHeight - el.scrollTop - el.clientHeight > 48) setFollow(false);
  };

  const blocks = [];
  for (let start = 0; start < count; start += CHUNK) {
    blocks.push(<Block key={start} start={start} lines={lines.current.slice(start, Math.min(start + CHUNK, count))} />);
  }
  return (
    <section className="card log-panel" aria-labelledby="log-heading">
      <div className="log-head">
        <h2 className="section-title" id="log-heading">
          Output <span className="count">{plural(count, "line", "lines")}</span>
        </h2>
        <div className="actions">
          <label className="switch">
            <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
            <span className="track" aria-hidden="true" />
            Follow the output
          </label>
          <label className="switch">
            <input type="checkbox" checked={wrap} onChange={(e) => setWrap(e.target.checked)} />
            <span className="track" aria-hidden="true" />
            Wrap long lines
          </label>
        </div>
      </div>
      <pre className={`log${wrap ? " wrap" : ""}`} id="log" tabIndex={0} role="region" aria-label="The lines of the output"
           ref={pre} onScroll={onScroll}>
        {count ? blocks : <span className="log-empty">No output yet.</span>}
      </pre>
    </section>
  );
}
