package com.laxman.xrayluggage.controller;

import java.util.List;
import java.util.Map;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.laxman.xrayluggage.model.SystemSettings;
import com.laxman.xrayluggage.repository.SystemSettingsRepository;

// Replaces: routes/settings.py
// Handles reading and updating system-wide settings
@RestController
@RequestMapping("/api/settings")
public class SettingsController {

    private final SystemSettingsRepository settingsRepo;

    public SettingsController(SystemSettingsRepository settingsRepo) {
        this.settingsRepo = settingsRepo;
    }

    // GET /api/settings
    // Replaces: @settings_router.get("/") in settings.py
    // Returns the single settings row (seeded at startup)
    @GetMapping
    public ResponseEntity<?> getSettings() {
        List<SystemSettings> all = settingsRepo.findAll();

        if (all.isEmpty()) {
            // Replaces: if not result.scalar(): session.add(SystemSettings()) in database.py
            SystemSettings defaults = new SystemSettings();
            return ResponseEntity.ok(settingsRepo.save(defaults));
        }

        return ResponseEntity.ok(all.get(0));
    }

    // PUT /api/settings
    // Replaces: @settings_router.put("/") in settings.py
    // Updates the settings row with values from request body
    @PutMapping
    public ResponseEntity<?> updateSettings(@RequestBody SystemSettings updated) {
        List<SystemSettings> all = settingsRepo.findAll();

        if (all.isEmpty()) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("detail", "Settings not initialized"));
        }

        SystemSettings existing = all.get(0);

        // Replaces: for key, value in updated.dict(exclude_unset=True).items():
        //               setattr(settings, key, value)
        if (updated.getConfidenceThreshold() != null)
            existing.setConfidenceThreshold(updated.getConfidenceThreshold());

        if (updated.getConfirmFrames() != null)
            existing.setConfirmFrames(updated.getConfirmFrames());

        if (updated.getCameraIndex() != null)
            existing.setCameraIndex(updated.getCameraIndex());

        // Replaces: await session.commit(); await session.refresh(settings)
        return ResponseEntity.ok(settingsRepo.save(existing));
    }
}
