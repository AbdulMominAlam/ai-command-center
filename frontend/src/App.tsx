import { useEffect, useState } from "react";
import { Shell, type Page } from "./components/Shell";
import { ChatPage, type ChatMessage } from "./pages/Chat";
import { Connect } from "./pages/Connect";
import { LabelPage } from "./pages/Label";
import { TasksPage } from "./pages/Tasks";
import { TodayPage } from "./pages/Today";
import { useMe } from "./queries";

// Hash routes (#/tasks) so page URLs never clash with the API paths the dev server proxies.
function pageFromHash(): Page {
  const h = window.location.hash.replace(/^#\/?/, "");
  return h === "tasks" || h === "chat" || h === "label" ? h : "today";
}

export function App() {
  const me = useMe();
  const [page, setPage] = useState<Page>(pageFromHash);
  // Lives here, not in ChatPage, so the conversation survives switching tabs.
  const [chat, setChat] = useState<ChatMessage[]>([]);

  useEffect(() => {
    const onHash = () => {
      setPage(pageFromHash());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (me.isPending) return null;
  if (me.isError)
    return (
      <p className="p-8 text-body text-accent">
        Can't reach the backend ({me.error.message}). Is it running on port 8000?
      </p>
    );
  if (me.data === null) return <Connect />;

  return (
    <Shell page={page} email={me.data.email}>
      {page === "today" && <TodayPage />}
      {page === "tasks" && <TasksPage />}
      {page === "chat" && <ChatPage messages={chat} setMessages={setChat} />}
      {page === "label" && <LabelPage />}
    </Shell>
  );
}
