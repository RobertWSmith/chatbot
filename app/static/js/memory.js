import { jsonHeaders } from "./app.js";

document.querySelectorAll(".memory-item").forEach((item) => {
  const id = item.dataset.memoryId;
  const approve = item.querySelector(".memory-approve");
  const reject = item.querySelector(".memory-reject");

  async function send(action) {
    const response = await fetch(`/api/memories/${id}/${action}`, {
      method: "POST",
      headers: jsonHeaders(),
    });
    if (response.ok) {
      window.location.reload();
    }
  }

  if (approve) approve.addEventListener("click", () => send("approve"));
  if (reject) reject.addEventListener("click", () => send("reject"));
});
