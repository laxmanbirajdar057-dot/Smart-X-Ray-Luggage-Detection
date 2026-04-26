package com.laxman.xrayluggage.model;

import java.time.LocalDateTime;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import lombok.Data;

// Replaces: class SystemSettings(Base) in database.py
@Entity
@Table(name = "system_settings")
@Data
public class SystemSettings {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    // Replaces: confidence_threshold = Column(Float, default=0.80)
    @Column(name = "confidence_threshold")
    private Double confidenceThreshold = 0.80;

    // Replaces: confirm_frames = Column(Integer, default=12)
    @Column(name = "confirm_frames")
    private Integer confirmFrames = 12;

    // Replaces: camera_index = Column(Integer, default=0)
    @Column(name = "camera_index")
    private Integer cameraIndex = 0;

    // Replaces: updated_at = Column(DateTime, default=datetime.utcnow)
    @Column(name = "updated_at")
    private LocalDateTime updatedAt = LocalDateTime.now();

    // Called automatically before every save/update
    // Replaces the Python default=datetime.utcnow on updated_at
    @PreUpdate
    public void onUpdate() {
        this.updatedAt = LocalDateTime.now();
    }
}

