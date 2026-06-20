import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ChatView from "./ChatView";

vi.mock("../api/client", () => ({
  api: {
    listConversations: vi.fn().mockResolvedValue([]),
    getConversation: vi.fn().mockResolvedValue({ meta: {}, messages: [] }),
    createConversation: vi.fn().mockResolvedValue({ id: "c1" }),
    chat: vi.fn().mockResolvedValue(undefined),
  },
}));
import { api } from "../api/client";

function renderChat() {
  render(
    <MemoryRouter>
      <ChatView keySet={true} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.listConversations as any).mockResolvedValue([]);
  (api.createConversation as any).mockResolvedValue({ id: "c1" });
  (api.chat as any).mockResolvedValue(undefined);
});

test("Shift+Enter does not send", async () => {
  renderChat();
  await waitFor(() => expect(api.listConversations).toHaveBeenCalled());
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
  expect(api.chat).not.toHaveBeenCalled();
});

test("Enter sends the message", async () => {
  renderChat();
  await waitFor(() => expect(api.listConversations).toHaveBeenCalled());
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("c1", "hello", expect.any(Function)));
});
