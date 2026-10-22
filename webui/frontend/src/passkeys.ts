// Passkeys in the browser: navigator.credentials with the options the server sends (py_webauthn's JSON, in which
// every binary value is base64url), and the answer sent back in the same JSON form (PublicKeyCredential's
// toJSON() shape), built by hand so it works in browsers that don't have toJSON() yet.

export function toBase64url(data: ArrayBuffer | ArrayBufferView): string {
  const bytes = ArrayBuffer.isView(data) ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength) : new Uint8Array(data);
  let text = "";
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function fromBase64url(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const text = atob(base64);
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
  return bytes.buffer;
}

// Why this page can't use passkeys, or null if it can. rpId is the host name the server's passkeys belong to;
// publicUrl is USAS_PUBLIC_URL (empty: http://localhost).
export function passkeyProblem(rpId: string, publicUrl: string): string | null {
  const where = publicUrl || "http://localhost:<port>";
  if (!window.isSecureContext) {
    return `Passkeys need a secure page: HTTPS, or http://localhost. Open the UI at ${where} to use them.`;
  }
  if (typeof window.PublicKeyCredential !== "function" || !navigator.credentials) {
    return "This browser doesn't support passkeys.";
  }
  const host = window.location.hostname;
  if (host !== rpId && !host.endsWith(`.${rpId}`)) {
    return `Passkeys here belong to ${rpId}. Open the UI at ${where} to use them.`;
  }
  return null;
}

interface Descriptor {
  id: string;
  type: "public-key";
  transports?: AuthenticatorTransport[];
}

export interface CreationOptionsJSON {
  challenge: string;
  rp: PublicKeyCredentialRpEntity;
  user: { id: string; name: string; displayName: string };
  pubKeyCredParams: PublicKeyCredentialParameters[];
  timeout?: number;
  excludeCredentials?: Descriptor[];
  authenticatorSelection?: AuthenticatorSelectionCriteria;
  attestation?: AttestationConveyancePreference;
}

export interface RequestOptionsJSON {
  challenge: string;
  rpId?: string;
  timeout?: number;
  allowCredentials?: Descriptor[];
  userVerification?: UserVerificationRequirement;
}

function descriptors(list: Descriptor[] | undefined): PublicKeyCredentialDescriptor[] | undefined {
  return list?.map((d) => ({ type: d.type, id: fromBase64url(d.id), transports: d.transports }));
}

// What went wrong, in words, from the DOMException the browser throws.
export function passkeyError(error: unknown, rpId: string): string {
  const { name, message } = (typeof error === "object" && error ? error : {}) as { name?: unknown; message?: unknown };
  switch (name) {
    case "NotAllowedError":
    case "AbortError":
      return "The passkey request was cancelled or timed out. Try again.";
    case "InvalidStateError":
      return "This authenticator has a passkey for this UI already.";
    case "SecurityError":
      return `The browser refused: this page's address doesn't belong to ${rpId}.`;
    case "NotSupportedError":
      return "This authenticator can't make a passkey this UI accepts.";
    default:
      return `The passkey request failed${typeof message === "string" && message ? `: ${message}` : "."}`;
  }
}

// Makes a passkey; returns the answer for the server, as JSON text.
export async function createPasskey(options: CreationOptionsJSON): Promise<string> {
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...options,
    challenge: fromBase64url(options.challenge),
    user: { ...options.user, id: fromBase64url(options.user.id) },
    excludeCredentials: descriptors(options.excludeCredentials),
  };
  const credential = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null;
  if (!credential) throw new DOMException("No passkey was made.", "NotAllowedError");
  const response = credential.response as AuthenticatorAttestationResponse;
  return JSON.stringify({
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      attestationObject: toBase64url(response.attestationObject),
      transports: response.getTransports?.() ?? [],
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  });
}

// Uses a passkey; returns the answer for the server, as JSON text.
export async function getPasskey(options: RequestOptionsJSON): Promise<string> {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...options,
    challenge: fromBase64url(options.challenge),
    allowCredentials: descriptors(options.allowCredentials),
  };
  const credential = await navigator.credentials.get({ publicKey }) as PublicKeyCredential | null;
  if (!credential) throw new DOMException("No passkey was chosen.", "NotAllowedError");
  const response = credential.response as AuthenticatorAssertionResponse;
  return JSON.stringify({
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      authenticatorData: toBase64url(response.authenticatorData),
      signature: toBase64url(response.signature),
      userHandle: response.userHandle ? toBase64url(response.userHandle) : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  });
}
