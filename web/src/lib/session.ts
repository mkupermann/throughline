let csrf = "";
export const sessionToken = () => csrf;
export const setSessionToken = (value: string | null) => {
  csrf = value ?? "";
};
