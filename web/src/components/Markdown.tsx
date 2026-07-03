import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Renders assistant text as GitHub-flavored Markdown (tables, lists, code,
// headings, links). react-markdown does NOT render raw HTML by default, so
// model output can't inject scripts. Tables are wrapped so they scroll inside
// the narrow agent panel instead of overflowing.
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: (props) => (
            <a href={props.href} target="_blank" rel="noreferrer noopener">
              {props.children}
            </a>
          ),
          table: (props) => (
            <div className="md-table-wrap">
              <table>{props.children}</table>
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
