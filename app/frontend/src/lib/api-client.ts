import axios, { AxiosInstance } from 'axios';

interface AuthResponse {
  user: {
    id: number;
    username: string;
    email: string | null;
    role: string;
    is_active: boolean;
    created_at: string;
  };
  token: {
    access_token: string;
    token_type: string;
  };
}

interface ApiClient extends AxiosInstance {
  login: (username: string, password: string) => Promise<AuthResponse>;
  register: (username: string, email: string | null, password: string) => Promise<AuthResponse>;
}

const instance = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add token to requests if it exists
instance.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Add convenience methods to the client
const apiClient = instance as ApiClient;

apiClient.login = async (username: string, password: string) => {
  const { data } = await apiClient.post<AuthResponse>('/auth/login', { username, password });
  return data;
};

apiClient.register = async (username: string, email: string | null, password: string) => {
  const { data } = await apiClient.post<AuthResponse>('/auth/register', { username, email, password });
  return data;
};

export default apiClient;
