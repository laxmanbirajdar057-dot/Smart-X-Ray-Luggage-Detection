package com.laxman.xrayluggage.controller;

import java.util.List;
import java.util.Map;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.core.io.ClassPathResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;

import com.laxman.xrayluggage.config.JwtUtil;
import com.laxman.xrayluggage.dto.AuthDTO;
import com.laxman.xrayluggage.model.Detection;
import com.laxman.xrayluggage.repository.DetectionRepository;

@RestController
@RequestMapping("/api/admin")
public class AdminController {

    private final JwtUtil jwtUtil;
    private final DetectionRepository detectionRepo;

    @Value("${app.admin.username}")
    private String adminUsername;

    @Value("${app.admin.password}")
    private String adminPassword;

    public AdminController(JwtUtil jwtUtil, DetectionRepository detectionRepo) {
        this.jwtUtil = jwtUtil;
        this.detectionRepo = detectionRepo;
    }

    @PostMapping("/login")
    public ResponseEntity<?> login(@RequestBody AuthDTO.LoginRequest request) {
        if (!request.getUsername().equals(adminUsername) ||
                !request.getPassword().equals(adminPassword)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("detail", "Invalid credentials"));
        }
        String token = jwtUtil.createToken(request.getUsername());
        return ResponseEntity.ok(new AuthDTO.LoginResponse(token));
    }

    @GetMapping("/detections")
    public List<Detection> getAllDetections() {
        return detectionRepo.findAllOrderByTimestampDesc();
    }

    @DeleteMapping("/detections/{id}")
    public ResponseEntity<?> deleteDetection(@PathVariable Long id) {
        if (!detectionRepo.existsById(id)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("detail", "Detection not found"));
        }
        detectionRepo.deleteById(id);
        return ResponseEntity.ok(Map.of("message", "deleted"));
    }

    @GetMapping(value = "/admin", produces = MediaType.TEXT_HTML_VALUE)
    public Resource adminPage() {
        return new ClassPathResource("static/admin.html");
    }
}