// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Binary protocol helpers for CellScope (CONTRACT sections 4 & 9).
 *
 * All binary responses are `application/octet-stream`, little-endian, with no
 * padding. Coordinates and continuous per-cell scalars travel as Float32;
 * categorical codes and cell indices travel as Int32. The number of
 * observations is always echoed in the `X-Cellscope-N-Obs` header and asserted
 * client-side.
 *
 * These helpers read the `X-Cellscope-*` headers by their exact contract names.
 */

import type { Bounds } from "../types";

/**
 * Error thrown when a binary response violates the contract (e.g. its byte
 * length disagrees with the echoed `X-Cellscope-N-Obs` header).
 */
export class BinaryProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BinaryProtocolError";
  }
}

/**
 * Detect whether the host is little-endian. The contract mandates little-endian
 * wire data; on the (effectively non-existent) big-endian host the zero-copy
 * typed-array views below would be wrong, so we guard against it.
 */
const HOST_LITTLE_ENDIAN: boolean = (() => {
  const probe = new Uint8Array(new Uint16Array([1]).buffer);
  return probe[0] === 1;
})();

/**
 * Interpret an {@link ArrayBuffer} as a little-endian `Float32Array`.
 *
 * On little-endian hosts this is a zero-copy view; on big-endian hosts the
 * bytes are reinterpreted via {@link DataView} to honor the wire order.
 *
 * @param buf - Raw bytes from a binary response body. Its byte length must be a
 *   multiple of 4.
 * @returns A `Float32Array` of length `buf.byteLength / 4`.
 * @throws {@link BinaryProtocolError} if the byte length is not a multiple of 4.
 */
export function parseFloat32(buf: ArrayBuffer): Float32Array {
  if (buf.byteLength % 4 !== 0) {
    throw new BinaryProtocolError(
      `Float32 buffer length ${buf.byteLength} is not a multiple of 4`,
    );
  }
  const length = buf.byteLength / 4;
  if (HOST_LITTLE_ENDIAN) {
    return new Float32Array(buf);
  }
  const view = new DataView(buf);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    out[i] = view.getFloat32(i * 4, true);
  }
  return out;
}

/**
 * Interpret an {@link ArrayBuffer} as a little-endian `Int32Array`.
 *
 * On little-endian hosts this is a zero-copy view; on big-endian hosts the
 * bytes are reinterpreted via {@link DataView} to honor the wire order.
 *
 * @param buf - Raw bytes from a binary response body. Its byte length must be a
 *   multiple of 4.
 * @returns An `Int32Array` of length `buf.byteLength / 4`.
 * @throws {@link BinaryProtocolError} if the byte length is not a multiple of 4.
 */
export function parseInt32(buf: ArrayBuffer): Int32Array {
  if (buf.byteLength % 4 !== 0) {
    throw new BinaryProtocolError(
      `Int32 buffer length ${buf.byteLength} is not a multiple of 4`,
    );
  }
  const length = buf.byteLength / 4;
  if (HOST_LITTLE_ENDIAN) {
    return new Int32Array(buf);
  }
  const view = new DataView(buf);
  const out = new Int32Array(length);
  for (let i = 0; i < length; i += 1) {
    out[i] = view.getInt32(i * 4, true);
  }
  return out;
}

/**
 * Encode an `Int32Array` to a little-endian {@link ArrayBuffer} for upload as
 * the body of a selection-register request (CONTRACT section 4.7).
 *
 * @param arr - Cell indices to encode.
 * @returns A freshly allocated little-endian buffer of `arr.length * 4` bytes.
 */
export function encodeInt32(arr: Int32Array): ArrayBuffer {
  if (HOST_LITTLE_ENDIAN) {
    // Copy into a tightly-sized buffer so any sub-array view does not leak its
    // backing buffer's extra bytes.
    const out = new Int32Array(arr.length);
    out.set(arr);
    return out.buffer;
  }
  const out = new ArrayBuffer(arr.length * 4);
  const view = new DataView(out);
  for (let i = 0; i < arr.length; i += 1) {
    view.setInt32(i * 4, arr[i], true);
  }
  return out;
}

/**
 * Parse an `X-Cellscope-Bounds` header value into a {@link Bounds} tuple.
 *
 * The header is four comma-separated floats `minX,minY,maxX,maxY`
 * (CONTRACT sections 4.5 / 4.8).
 *
 * @param headerValue - The raw header string, or `null` if absent.
 * @returns The parsed bounds tuple.
 * @throws {@link BinaryProtocolError} if the header is missing or malformed.
 */
export function parseBounds(headerValue: string | null): Bounds {
  if (headerValue === null) {
    throw new BinaryProtocolError("Missing X-Cellscope-Bounds header");
  }
  const parts = headerValue.split(",").map((p) => Number(p.trim()));
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) {
    throw new BinaryProtocolError(
      `Malformed X-Cellscope-Bounds header: "${headerValue}"`,
    );
  }
  return [parts[0], parts[1], parts[2], parts[3]];
}

/**
 * Read an integer-valued response header by name.
 *
 * @param headers - The response headers.
 * @param name - The exact header name (e.g. `"X-Cellscope-N-Obs"`).
 * @returns The parsed integer.
 * @throws {@link BinaryProtocolError} if the header is missing or not an integer.
 */
export function getHeaderInt(headers: Headers, name: string): number {
  const raw = headers.get(name);
  if (raw === null) {
    throw new BinaryProtocolError(`Missing ${name} header`);
  }
  const value = Number(raw);
  if (!Number.isInteger(value)) {
    throw new BinaryProtocolError(`Header ${name} is not an integer: "${raw}"`);
  }
  return value;
}

/**
 * Read a float-valued response header by name.
 *
 * @param headers - The response headers.
 * @param name - The exact header name (e.g. `"X-Cellscope-Min"`).
 * @returns The parsed float.
 * @throws {@link BinaryProtocolError} if the header is missing or not a number.
 */
export function getHeaderFloat(headers: Headers, name: string): number {
  const raw = headers.get(name);
  if (raw === null) {
    throw new BinaryProtocolError(`Missing ${name} header`);
  }
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new BinaryProtocolError(`Header ${name} is not a number: "${raw}"`);
  }
  return value;
}

/**
 * Read a required string-valued response header by name.
 *
 * @param headers - The response headers.
 * @param name - The exact header name (e.g. `"X-Cellscope-Gene"`).
 * @returns The header value.
 * @throws {@link BinaryProtocolError} if the header is missing.
 */
export function getHeaderString(headers: Headers, name: string): string {
  const raw = headers.get(name);
  if (raw === null) {
    throw new BinaryProtocolError(`Missing ${name} header`);
  }
  return raw;
}

/**
 * Assert that the number of decoded elements matches the echoed
 * `X-Cellscope-N-Obs` header (CONTRACT section 9, invariant 2).
 *
 * @param actual - The element count derived from `byteLength / 4`.
 * @param headerN - The integer value of the `X-Cellscope-N-Obs` header.
 * @throws {@link BinaryProtocolError} if the counts disagree.
 */
export function assertNObs(actual: number, headerN: number): void {
  if (actual !== headerN) {
    throw new BinaryProtocolError(
      `n_obs mismatch: decoded ${actual} elements but X-Cellscope-N-Obs is ${headerN}`,
    );
  }
}
