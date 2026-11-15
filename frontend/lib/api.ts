export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public fields: Record<string, string> = {},
  ) {
    super(message);
  }
}

export async function errorFromResponse(response: Response): Promise<ApiError> {
  const data = await response.json().catch(() => ({}));
  const fields: Record<string, string> = {};
  if (Array.isArray(data.detail)) {
    for (const detail of data.detail) {
      const field = detail.loc?.at(-1);
      if (typeof field === "string")
        fields[field] = detail.msg || "Periksa nilai ini.";
    }
  }
  if (data.error?.param) fields[data.error.param] = data.error.message;
  const fallback =
    response.status === 422
      ? "Ada nilai yang belum sesuai. Periksa field yang ditandai."
      : response.status >= 500
        ? "Server belum dapat memproses permintaan. Coba lagi sebentar."
        : `Permintaan gagal (HTTP ${response.status}).`;
  return new ApiError(data.error?.message || fallback, response.status, fields);
}

export function messageFor(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "Koneksi ke dashboard terputus. Periksa koneksi, lalu coba lagi.";
}
