// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * WebSocket job client for CellScope (CONTRACT section 5).
 *
 * Wraps a single WebSocket connection to `/api/ws/jobs`. The scheme (`ws://`
 * vs `wss://`) and host are derived from `window.location` so the socket
 * follows the page origin. Frames are JSON text in both directions:
 * - Client -> server: {@link JobSubmit} / {@link JobCancel}.
 * - Server -> client: the {@link ServerJobMessage} discriminated union.
 *
 * The client connects on demand, buffers nothing, and exposes a simple
 * subscription model via {@link JobClient.onMessage}.
 */

import type {
  ClientJobMessage,
  JobCancel,
  JobParams,
  JobSubmit,
  JobType,
  ServerJobMessage,
} from "../types";

/** Path of the job WebSocket endpoint (CONTRACT section 5). */
const WS_PATH = "/api/ws/jobs";

/**
 * Callback invoked for each parsed server -> client message.
 */
export type JobMessageHandler = (message: ServerJobMessage) => void;

/**
 * Disposer returned by {@link JobClient.onMessage}; call to unsubscribe.
 */
export type Unsubscribe = () => void;

/**
 * Derive the WebSocket URL for the job endpoint from the current page location.
 *
 * Uses `wss://` when the page is served over HTTPS, else `ws://`, preserving
 * host and port.
 *
 * @param location - The location to derive from; defaults to `window.location`.
 * @returns The fully-qualified WebSocket URL.
 */
export function jobsWebSocketUrl(
  location: Location = window.location,
): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${WS_PATH}`;
}

/**
 * Manages a WebSocket connection to the job endpoint and dispatches typed
 * server messages to subscribers (CONTRACT section 5).
 */
export class JobClient {
  private readonly url: string;

  private socket: WebSocket | null = null;

  private readonly handlers = new Set<JobMessageHandler>();

  /**
   * Construct a job client.
   *
   * @param url - Optional explicit WebSocket URL; defaults to the one derived
   *   from `window.location` via {@link jobsWebSocketUrl}.
   */
  constructor(url: string = jobsWebSocketUrl()) {
    this.url = url;
  }

  /**
   * Whether the underlying socket is open and ready to send.
   */
  get isConnected(): boolean {
    return this.socket !== null && this.socket.readyState === WebSocket.OPEN;
  }

  /**
   * Open the WebSocket connection, resolving once it is established.
   *
   * If a connection is already open (or opening) this resolves without creating
   * a new socket, so it is safe to call before every {@link submit}.
   *
   * @returns A promise that resolves when the socket is open.
   * @throws An `Error` if the socket fails to open or closes during connect.
   */
  connect(): Promise<void> {
    if (this.socket !== null) {
      const state = this.socket.readyState;
      if (state === WebSocket.OPEN) {
        return Promise.resolve();
      }
      if (state === WebSocket.CONNECTING) {
        return this.waitForOpen(this.socket);
      }
      // CLOSING or CLOSED: drop the stale socket and reconnect below.
      this.teardownSocket();
    }

    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.onmessage = (event: MessageEvent): void => {
      this.dispatch(event.data);
    };
    socket.onclose = (): void => {
      if (this.socket === socket) {
        this.socket = null;
      }
    };
    return this.waitForOpen(socket);
  }

  /**
   * Submit a recompute job, connecting first if necessary (CONTRACT section 5).
   *
   * @param jobType - Which compute to run.
   * @param datasetId - The dataset to operate on.
   * @param selectionId - The registered selection the job is scoped to.
   * @param params - Job-type-specific parameters.
   * @returns A promise resolving once the submit frame is sent.
   */
  async submit(
    jobType: JobType,
    datasetId: string,
    selectionId: string,
    params: JobParams,
  ): Promise<void> {
    const message: JobSubmit = {
      action: "submit",
      job_type: jobType,
      dataset_id: datasetId,
      selection_id: selectionId,
      params,
    };
    await this.send(message);
  }

  /**
   * Request cancellation of a running job, connecting first if necessary
   * (CONTRACT section 5).
   *
   * @param jobId - The id of the job to cancel.
   * @returns A promise resolving once the cancel frame is sent.
   */
  async cancel(jobId: string): Promise<void> {
    const message: JobCancel = { action: "cancel", job_id: jobId };
    await this.send(message);
  }

  /**
   * Subscribe to parsed server -> client messages.
   *
   * @param callback - Invoked for each {@link ServerJobMessage}.
   * @returns A disposer that unsubscribes the callback.
   */
  onMessage(callback: JobMessageHandler): Unsubscribe {
    this.handlers.add(callback);
    return () => {
      this.handlers.delete(callback);
    };
  }

  /**
   * Close the WebSocket connection and detach its handlers. Message
   * subscriptions registered via {@link onMessage} are preserved so the client
   * can be reconnected and reused.
   */
  close(): void {
    this.teardownSocket();
  }

  /**
   * Serialize and send a client frame, connecting on demand.
   *
   * @param message - The frame to send.
   */
  private async send(message: ClientJobMessage): Promise<void> {
    await this.connect();
    if (this.socket === null || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error("Job WebSocket is not open");
    }
    this.socket.send(JSON.stringify(message));
  }

  /**
   * Parse an incoming frame and dispatch it to all subscribers. Non-JSON or
   * structurally invalid frames are dropped silently (a single bad frame must
   * not break the stream).
   *
   * @param data - The raw frame payload.
   */
  private dispatch(data: unknown): void {
    if (typeof data !== "string") {
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      return;
    }
    if (!isServerJobMessage(parsed)) {
      return;
    }
    for (const handler of this.handlers) {
      handler(parsed);
    }
  }

  /**
   * Wrap a connecting socket's open/error/close events in a promise.
   *
   * @param socket - The socket to await.
   * @returns A promise resolving on open and rejecting on failure.
   */
  private waitForOpen(socket: WebSocket): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const cleanup = (): void => {
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("error", onError);
        socket.removeEventListener("close", onClose);
      };
      const onOpen = (): void => {
        cleanup();
        resolve();
      };
      const onError = (): void => {
        cleanup();
        reject(new Error("Job WebSocket connection failed"));
      };
      const onClose = (): void => {
        cleanup();
        reject(new Error("Job WebSocket closed before opening"));
      };
      socket.addEventListener("open", onOpen);
      socket.addEventListener("error", onError);
      socket.addEventListener("close", onClose);
    });
  }

  /**
   * Detach handlers from the current socket and close it.
   */
  private teardownSocket(): void {
    const socket = this.socket;
    this.socket = null;
    if (socket === null) {
      return;
    }
    socket.onmessage = null;
    socket.onclose = null;
    const state = socket.readyState;
    if (state === WebSocket.OPEN || state === WebSocket.CONNECTING) {
      socket.close();
    }
  }
}

/**
 * Runtime guard validating that a parsed value is a {@link ServerJobMessage}.
 *
 * @param value - The candidate value (typically `JSON.parse` output).
 * @returns `true` if `value` is a recognized server message.
 */
function isServerJobMessage(value: unknown): value is ServerJobMessage {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const record = value as Record<string, unknown>;
  if (typeof record.type !== "string") {
    return false;
  }
  // `error` and `cancelled` frames may carry a null `job_id` (validation-path
  // errors are emitted before any job is accepted), so those variants accept
  // `string | null`; all other variants require a string `job_id`.
  const jobIdIsString = typeof record.job_id === "string";
  const jobIdIsStringOrNull = jobIdIsString || record.job_id === null;
  switch (record.type) {
    case "accepted":
      return jobIdIsString && typeof record.job_type === "string";
    case "progress":
      return (
        jobIdIsString &&
        typeof record.step === "string" &&
        typeof record.progress === "number" &&
        typeof record.message === "string"
      );
    case "completed":
      return (
        jobIdIsString &&
        typeof record.job_type === "string" &&
        typeof record.result_url === "string" &&
        typeof record.summary === "object" &&
        record.summary !== null
      );
    case "error":
      return jobIdIsStringOrNull && typeof record.error === "string";
    case "cancelled":
      return jobIdIsStringOrNull;
    default:
      return false;
  }
}
