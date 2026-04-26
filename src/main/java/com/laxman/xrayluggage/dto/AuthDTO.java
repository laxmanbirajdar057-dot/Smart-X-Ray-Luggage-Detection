package com.laxman.xrayluggage.dto;

public class AuthDTO {

    public static class LoginRequest {
        private String username;
        private String password;

        // Manually written getters — no Lombok needed
        public String getUsername() {
            return username;
        }

        public String getPassword() {
            return password;
        }

        public void setUsername(String username) {
            this.username = username;
        }

        public void setPassword(String password) {
            this.password = password;
        }
    }

    public static class LoginResponse {
        private String accessToken;
        private String tokenType = "bearer";

        public LoginResponse(String accessToken) {
            this.accessToken = accessToken;
        }

        public String getAccessToken() {
            return accessToken;
        }

        public String getTokenType() {
            return tokenType;
        }
    }
}