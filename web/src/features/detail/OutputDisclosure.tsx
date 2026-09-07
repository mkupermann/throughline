import { t } from "@/lib/ui";
import "./output-disclosure.css";

/** Keep recorded output available without expanding the conversation by default. */
export function OutputDisclosure({ body }: { body: string }) {
  return <details className="output-disclosure">
    <summary>{t("Result / tool output")}</summary>
    <pre>{body}</pre>
  </details>;
}
