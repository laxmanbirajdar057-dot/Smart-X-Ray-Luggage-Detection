package com.laxman.xrayluggage.controller;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.laxman.xrayluggage.model.Detection;
import com.laxman.xrayluggage.repository.DetectionRepository;

// Replaces: routes/detections.py
// Handles CRUD operations for X-ray scan detections
@RestController
@RequestMapping("/api/detections")
public class DetectionController {

    private final DetectionRepository detectionRepo;

    // Constructor injection (preferred over @Autowired)
    // Replaces: db: AsyncSession = Depends(get_db) in Python
    public DetectionController(DetectionRepository detectionRepo) {
        this.detectionRepo = detectionRepo;
    }

    // GET /api/detections
    // Replaces: @detections_router.get("/") in detections.py
    @GetMapping
    public List<Detection> getAllDetections() {
        return detectionRepo.findAllOrderByTimestampDesc();
    }

    // GET /api/detections/{id}
    // Replaces: @detections_router.get("/{detection_id}") in detections.py
    @GetMapping("/{id}")
    public ResponseEntity<?> getDetection(@PathVariable Long id) {
        // Replaces: result = await session.get(Detection, detection_id)
        return detectionRepo.findById(id)
                .<ResponseEntity<?>>map(ResponseEntity::ok)
                // Replaces: raise HTTPException(status_code=404) if not found
                .orElse(ResponseEntity.status(HttpStatus.NOT_FOUND)
                        .body(Map.of("detail", "Detection not found")));
    }

    // POST /api/detections
    // Replaces: @detections_router.post("/") in detections.py
    // @RequestBody reads JSON from the request and maps it to Detection object
    @PostMapping
    public ResponseEntity<Detection> createDetection(@RequestBody Detection detection) {
        // Auto-set timestamp if not provided
        if (detection.getTimestamp() == null) {
            detection.setTimestamp(LocalDateTime.now());
        }

        // Replaces: session.add(detection); await session.commit(); await session.refresh(detection)
        Detection saved = detectionRepo.save(detection);

        // 201 Created with the saved object (includes auto-generated ID)
        return ResponseEntity.status(HttpStatus.CREATED).body(saved);
    }

    // DELETE /api/detections/{id}
    // Replaces: @detections_router.delete("/{detection_id}") in detections.py
    @DeleteMapping("/{id}")
    public ResponseEntity<?> deleteDetection(@PathVariable Long id) {
        if (!detectionRepo.existsById(id)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Map.of("detail", "Not found"));
        }
        detectionRepo.deleteById(id);
        return ResponseEntity.ok(Map.of("message", "deleted"));
    }

    // GET /api/detections/filter?className=knife
    // Replaces: @detections_router.get("/filter") with query params in detections.py
    @GetMapping("/filter")
    public List<Detection> filterByClass(@RequestParam String className) {
        // Replaces: session.execute(select(Detection).where(Detection.class_name == class_name))
        return detectionRepo.findByClassName(className);
    }
}

